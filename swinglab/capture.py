"""Synchrone Aufnahme beider Webcams mit RAM-Ringpuffer und Audio-Trigger.

Jede Kamera läuft in einem eigenen Thread und legt JPEG-komprimierte Frames mit
`perf_counter`-Zeitstempel in einen Ringpuffer. Beim Treffgeräusch (oder Enter)
werden pre/post-roll Sekunden beider Kameras auf eine gemeinsame Zeitachse
resampled und als synchrone MP4-Clips gespeichert.
"""
from __future__ import annotations

import collections
import json
import platform
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

VIEWS = ("dtl", "fo")


def _backend(name: str) -> int:
    name = (name or "auto").lower()
    if name == "auto":
        return cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    return {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "v4l2": cv2.CAP_V4L2,
            "any": cv2.CAP_ANY}[name]


def open_camera(cam: dict, backend: str = "auto") -> cv2.VideoCapture:
    cap = cv2.VideoCapture(int(cam["index"]), _backend(backend))
    if not cap.isOpened():
        raise RuntimeError(f"Kamera {cam['index']} lässt sich nicht öffnen")
    # Reihenfolge wichtig: FOURCC vor Auflösung/FPS (sonst YUY2 mit max. 30/5 fps)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cam.get("fourcc", "MJPG")))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
    cap.set(cv2.CAP_PROP_FPS, cam["fps"])
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_AUTOFOCUS, 1 if cam.get("autofocus") else 0)
    if cam.get("focus", -1) is not None and cam.get("focus", -1) >= 0:
        cap.set(cv2.CAP_PROP_FOCUS, cam["focus"])
    if cam.get("exposure") is not None:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if platform.system() != "Windows" else 0)
        cap.set(cv2.CAP_PROP_EXPOSURE, cam["exposure"])
    if cam.get("gain", -1) is not None and cam.get("gain", -1) >= 0:
        cap.set(cv2.CAP_PROP_GAIN, cam["gain"])
    return cap


def describe(cap: cv2.VideoCapture) -> str:
    fcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    fcc_s = "".join(chr((fcc >> 8 * i) & 0xFF) for i in range(4))
    return (f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
            f" @{cap.get(cv2.CAP_PROP_FPS):.0f} {fcc_s} exp={cap.get(cv2.CAP_PROP_EXPOSURE)}")


class CameraWorker(threading.Thread):
    def __init__(self, name: str, cam: dict, backend: str, buffer_s: float, jpeg_quality: int):
        super().__init__(daemon=True, name=f"cam-{name}")
        self.view, self.cam, self.backend = name, cam, backend
        self.buf: collections.deque = collections.deque(maxlen=int(cam["fps"] * buffer_s * 1.3))
        self.lock = threading.Lock()
        self.q = jpeg_quality
        self.running = threading.Event()
        self.measured_fps = 0.0
        self.latest: np.ndarray | None = None
        self.error: Exception | None = None
        self.info = ""

    def run(self):
        try:
            cap = open_camera(self.cam, self.backend)
        except Exception as e:  # noqa: BLE001
            self.error = e
            return
        self.info = describe(cap)
        self.running.set()
        params = [cv2.IMWRITE_JPEG_QUALITY, self.q]
        n, t0 = 0, time.perf_counter()
        while self.running.is_set():
            if not cap.grab():
                time.sleep(0.005)
                continue
            ts = time.perf_counter()
            ok, frame = cap.retrieve()
            if not ok:
                continue
            ok, jpg = cv2.imencode(".jpg", frame, params)
            with self.lock:
                self.buf.append((ts, jpg))
                self.latest = frame
            n += 1
            if ts - t0 >= 2.0:
                self.measured_fps, n, t0 = n / (ts - t0), 0, ts
        cap.release()

    def stop(self):
        self.running.clear()

    def snapshot(self, t_from: float, t_to: float) -> list[tuple[float, np.ndarray]]:
        with self.lock:
            return [(t, j) for t, j in self.buf if t_from <= t <= t_to]


def resample_to_timeline(frames: list[tuple[float, np.ndarray]], timeline: np.ndarray) -> list[int]:
    """Index des zeitlich nächsten Frames für jeden Zeitpunkt der gemeinsamen Zeitachse."""
    ts = np.array([t for t, _ in frames])
    idx = np.searchsorted(ts, timeline).clip(1, len(ts) - 1)
    left_closer = (timeline - ts[idx - 1]) < (ts[idx] - timeline)
    return list(np.where(left_closer, idx - 1, idx))


def write_clip(path: Path, frames: list, indices: list[int], fps: float) -> None:
    first = cv2.imdecode(frames[indices[0]][1], cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    cache: dict[int, np.ndarray] = {}
    for i in indices:
        if i not in cache:
            cache.clear()
            cache[i] = cv2.imdecode(frames[i][1], cv2.IMREAD_COLOR)
        vw.write(cache[i])
    vw.release()


class Recorder:
    def __init__(self, cfg: dict, session_dir: Path):
        self.cfg = cfg
        self.session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        cap = cfg["capture"]
        buf_s = cap["pre_roll_s"] + cap["post_roll_s"] + 2
        self.workers = {v: CameraWorker(v, cfg["cameras"][v], cap["backend"], buf_s,
                                        cap["jpeg_quality"]) for v in VIEWS}
        self.triggers: queue.Queue = queue.Queue()
        self.saved: list[Path] = []
        self._pool = None

    def next_swing_dir(self) -> Path:
        n = len(list(self.session_dir.glob("swing_*"))) + 1
        while (self.session_dir / f"swing_{n:03d}").exists():
            n += 1
        return self.session_dir / f"swing_{n:03d}"

    def trigger(self, t_impact: float, source: str):
        self.triggers.put((t_impact, source))

    def save(self, t_impact: float, source: str) -> Path | None:
        cap = self.cfg["capture"]
        t_impact_video = t_impact + cap["video_latency_s"]
        t0, t1 = t_impact_video - cap["pre_roll_s"], t_impact_video + cap["post_roll_s"]
        # warten bis Post-Roll im Puffer ist
        while time.perf_counter() < t1 + 0.1:
            time.sleep(0.02)
        fps = float(min(self.cfg["cameras"][v]["fps"] for v in VIEWS))
        timeline = np.arange(t0, t1, 1.0 / fps)
        snaps = {v: w.snapshot(t0 - 0.2, t1 + 0.2) for v, w in self.workers.items()}
        if any(len(s) < 10 for s in snaps.values()):
            print("  ! Zu wenige Frames im Puffer – Swing verworfen")
            return None
        out = self.next_swing_dir()
        out.mkdir()
        meta = {"recorded_at": datetime.now().isoformat(timespec="seconds"),
                "trigger": source, "fps": fps, "synced": True,
                "impact_time_s": float(t_impact_video - t0) if source == "audio" else None,
                "cameras": {}}
        for v, frames in snaps.items():
            idx = resample_to_timeline(frames, timeline)
            write_clip(out / f"{v}.mp4", frames, idx, fps)
            ts = np.array([t for t, _ in frames])
            meta["cameras"][v] = {"info": self.workers[v].info,
                                  "measured_fps": round(float(1 / np.median(np.diff(ts))), 1),
                                  "max_resample_error_ms": round(float(
                                      np.max(np.abs(ts[idx] - timeline)) * 1000), 1)}
        (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.saved.append(out)
        return out

    def run(self, preview: bool = False):
        for w in self.workers.values():
            w.start()
        deadline = time.time() + 10
        while time.time() < deadline and not all(
                w.running.is_set() or w.error for w in self.workers.values()):
            time.sleep(0.05)
        for v, w in self.workers.items():
            if w.error:
                raise RuntimeError(f"[{v}] {w.error}")
            print(f"  {v.upper():3s}: {w.info}")

        detector = None
        acfg = self.cfg["audio"]
        if acfg["enabled"]:
            from .audio import LiveDetectorConfig, LiveImpactDetector
            dcfg = LiveDetectorConfig(**{k: acfg[k] for k in (
                "samplerate", "highpass_hz", "threshold_factor", "min_peak", "refractory_s",
                "device")})
            detector = LiveImpactDetector(dcfg, lambda t: self.trigger(t, "audio"))
            try:
                detector.start()
                print("  Audio-Trigger aktiv – einfach schlagen.")
            except Exception as e:  # noqa: BLE001
                print(f"  ! Audio-Trigger nicht verfügbar ({e}); nur manuell per Enter.")
                detector = None

        def stdin_loop():
            for line in sys.stdin:
                cmd = line.strip().lower()
                if cmd in ("q", "quit", "exit"):
                    self.triggers.put((None, "quit"))
                    return
                self.trigger(time.perf_counter(), "manual")

        threading.Thread(target=stdin_loop, daemon=True).start()
        print("  Enter = letzten Swing manuell speichern, q + Enter = beenden\n")

        if self.cfg["capture"]["auto_analyze"]:
            from concurrent.futures import ProcessPoolExecutor
            self._pool = ProcessPoolExecutor(max_workers=1)

        last_status = 0.0
        try:
            while True:
                if preview:
                    self._show_preview(detector)
                try:
                    t, src = self.triggers.get(timeout=0.03)
                except queue.Empty:
                    if time.time() - last_status > 10:
                        fps = " ".join(f"{v}={w.measured_fps:.0f}fps"
                                       for v, w in self.workers.items())
                        print(f"  … bereit ({fps})", flush=True)
                        last_status = time.time()
                    continue
                if src == "quit":
                    break
                if src == "manual":  # manuell: Enter kurz nach dem Schlag -> Ende = jetzt
                    t = t - self.cfg["capture"]["post_roll_s"] - self.cfg["capture"]["video_latency_s"]
                print(f"  ● Swing erkannt ({src}) – speichere …", flush=True)
                out = self.save(t, src)
                if out:
                    print(f"  ✓ {out}")
                    if self._pool:
                        from .pipeline import analyze_swing_safe
                        self._pool.submit(analyze_swing_safe, str(out),
                                          self.cfg.get("_config_path"))
        except KeyboardInterrupt:
            pass
        finally:
            if detector:
                detector.stop()
            for w in self.workers.values():
                w.stop()
            if self._pool:
                print("  Warte auf laufende Analysen …")
                self._pool.shutdown(wait=True)
            cv2.destroyAllWindows()

    def _show_preview(self, detector):
        tiles = []
        for v, w in self.workers.items():
            f = w.latest
            if f is None:
                continue
            f = cv2.resize(f, (640, int(640 * f.shape[0] / f.shape[1])))
            h, wd = f.shape[:2]
            for x in (wd // 3, 2 * wd // 3):
                cv2.line(f, (x, 0), (x, h), (80, 80, 80), 1)
            cv2.putText(f, f"{v.upper()} {w.measured_fps:.0f}fps", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            tiles.append(f)
        if tiles:
            img = np.hstack(tiles) if len({t.shape for t in tiles}) == 1 else tiles[0]
            if detector:
                lvl = min(1.0, detector.level / max(detector.cfg.min_peak, 1e-6))
                cv2.rectangle(img, (0, img.shape[0] - 8), (int(img.shape[1] * lvl), img.shape[0]),
                              (0, 0, 255) if lvl >= 1 else (0, 200, 0), -1)
            cv2.imshow("swinglab", img)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                self.triggers.put((None, "quit"))


def list_cameras(backend: str = "auto", max_index: int = 8) -> list[tuple[int, str]]:
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, _backend(backend))
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                found.append((i, describe(cap)))
        cap.release()
    return found


def open_settings_dialog(cam: dict, backend: str = "auto"):
    """Öffnet den Treiber-Einstellungsdialog (nur DirectShow/Windows)."""
    cap = open_camera(cam, backend)
    cap.set(cv2.CAP_PROP_SETTINGS, 1)
    print("Einstellungsdialog geöffnet. Fenster schliessen und Enter drücken.")
    t_end = time.time() + 600
    while time.time() < t_end:
        ok, f = cap.read()
        if ok:
            cv2.imshow("Kamera", cv2.resize(f, (960, int(960 * f.shape[0] / f.shape[1]))))
        if cv2.waitKey(10) & 0xFF in (13, 27, ord("q")):
            break
    print("Aktuell:", describe(cap))
    cap.release()
    cv2.destroyAllWindows()


def new_session_dir(cfg: dict) -> Path:
    return Path(cfg["paths"]["data_dir"]) / "sessions" / datetime.now().strftime("%Y-%m-%d_%H%M")
