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
from typing import Callable

import cv2
import numpy as np

VIEWS = ("dtl", "fo")


def _backend(name: str) -> int:
    name = (name or "auto").lower()
    if name == "auto":
        return cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    return {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "v4l2": cv2.CAP_V4L2,
            "any": cv2.CAP_ANY}[name]


def is_file_source(cam: dict) -> bool:
    """Demo-/Testmodus: `index` darf ein Videopfad sein (wird in Echtzeit geloopt)."""
    return isinstance(cam["index"], str) and not cam["index"].strip().isdigit()


def open_camera(cam: dict, backend: str = "auto") -> cv2.VideoCapture:
    if is_file_source(cam):
        cap = cv2.VideoCapture(cam["index"])
        if not cap.isOpened():
            raise RuntimeError(f"Video {cam['index']} lässt sich nicht öffnen")
        return cap
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
    if cam.get("exposure") not in (None, "", "auto"):
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
        is_file = is_file_source(self.cam)
        period = 1.0 / float(cap.get(cv2.CAP_PROP_FPS) or self.cam["fps"]) if is_file else 0
        t_next = time.perf_counter()
        while self.running.is_set():
            if is_file:  # Echtzeit simulieren, am Ende von vorne
                t_next += period
                time.sleep(max(0.0, t_next - time.perf_counter()))
            if not cap.grab():
                if is_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
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


class CaptureEngine:
    """GUI-unabhängige Aufnahme-Engine.

    - `start()` öffnet beide Kameras (Live-Bild via `latest(view)`) und das Mikrofon.
    - Ist `armed` gesetzt, löst das Treffgeräusch das Speichern aus; `trigger_manual()` geht immer.
    - Gespeicherte Swings werden über `on_swing(path)` gemeldet, Meldungen über `on_status(text)`.
    """

    def __init__(self, cfg: dict, session_dir: Path | None = None,
                 on_swing: Callable[[Path], None] | None = None,
                 on_status: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.session_dir = session_dir
        self.on_swing = on_swing or (lambda p: None)
        self.on_status = on_status or (lambda s: None)
        cap = cfg["capture"]
        buf_s = cap["pre_roll_s"] + cap["post_roll_s"] + 2
        self.workers = {v: CameraWorker(v, cfg["cameras"][v], cap["backend"], buf_s,
                                        cap["jpeg_quality"]) for v in VIEWS}
        self.triggers: queue.Queue = queue.Queue()
        self.detector = None
        self.armed = False
        self.saving = False
        self._saver: threading.Thread | None = None
        self._stop = threading.Event()

    # --- Lebenszyklus -----------------------------------------------------
    def start(self, wait_s: float = 10.0) -> dict[str, str]:
        """Startet Kameras und Mikrofon. Gibt je Ansicht Info oder Fehlertext zurück."""
        for w in self.workers.values():
            w.start()
        deadline = time.time() + wait_s
        while time.time() < deadline and not all(
                w.running.is_set() or w.error for w in self.workers.values()):
            time.sleep(0.05)
        status = {v: (f"FEHLER: {w.error}" if w.error else w.info or "keine Antwort")
                  for v, w in self.workers.items()}
        acfg = self.cfg["audio"]
        if acfg["enabled"]:
            from .audio import LiveDetectorConfig, LiveImpactDetector
            dcfg = LiveDetectorConfig(**{k: acfg[k] for k in (
                "samplerate", "highpass_hz", "threshold_factor", "min_peak", "refractory_s",
                "device")})
            self.detector = LiveImpactDetector(dcfg, self._on_audio)
            try:
                self.detector.start()
                status["audio"] = "aktiv"
            except Exception as e:  # noqa: BLE001
                status["audio"] = f"FEHLER: {e}"
                self.detector = None
        self._stop.clear()
        self._saver = threading.Thread(target=self._save_loop, daemon=True, name="saver")
        self._saver.start()
        return status

    def stop(self):
        self._stop.set()
        self.triggers.put((None, "quit"))
        if self.detector:
            self.detector.stop()
            self.detector = None
        for w in self.workers.values():
            w.stop()
        if self._saver:
            self._saver.join(timeout=15)

    # --- Zugriff fürs UI ----------------------------------------------------
    def latest(self, view: str) -> np.ndarray | None:
        w = self.workers.get(view)
        return None if w is None else w.latest

    def fps(self, view: str) -> float:
        return self.workers[view].measured_fps

    @property
    def audio_level(self) -> float:
        """Pegel relativ zur Auslöseschwelle (1.0 = löst aus)."""
        if not self.detector:
            return 0.0
        return self.detector.level / max(self.detector.cfg.min_peak, 1e-6)

    # --- Trigger ------------------------------------------------------------
    def _on_audio(self, t: float):
        if self.armed and not self.saving:
            self.triggers.put((t, "audio"))

    def trigger_manual(self):
        """Speichert die letzten Sekunden (Aufruf kurz nach dem Schlag)."""
        cap = self.cfg["capture"]
        t = time.perf_counter() - cap["post_roll_s"] - cap["video_latency_s"]
        self.triggers.put((t, "manual"))

    def _save_loop(self):
        while not self._stop.is_set():
            t, src = self.triggers.get()
            if src == "quit":
                return
            if self.session_dir is None:
                self.on_status("Kein Session-Ordner – Swing verworfen")
                continue
            self.saving = True
            try:
                self.on_status(f"Swing erkannt ({src}) – speichere …")
                out = self.save(t, src)
                if out:
                    self.on_swing(out)
            except Exception as e:  # noqa: BLE001
                self.on_status(f"Speichern fehlgeschlagen: {e}")
            finally:
                self.saving = False

    def next_swing_dir(self) -> Path:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        n = len(list(self.session_dir.glob("swing_*"))) + 1
        while (self.session_dir / f"swing_{n:03d}").exists():
            n += 1
        return self.session_dir / f"swing_{n:03d}"

    def save(self, t_impact: float, source: str) -> Path | None:
        cap = self.cfg["capture"]
        t_impact_video = t_impact + cap["video_latency_s"]
        t0, t1 = t_impact_video - cap["pre_roll_s"], t_impact_video + cap["post_roll_s"]
        while time.perf_counter() < t1 + 0.1:  # warten bis Post-Roll im Puffer ist
            time.sleep(0.02)
        fps = float(min(self.cfg["cameras"][v]["fps"] for v in VIEWS))
        timeline = np.arange(t0, t1, 1.0 / fps)
        snaps = {v: w.snapshot(t0 - 0.2, t1 + 0.2) for v, w in self.workers.items()
                 if w.running.is_set()}
        snaps = {v: s for v, s in snaps.items() if len(s) >= 10}
        if not snaps:
            self.on_status("Zu wenige Frames im Puffer – Swing verworfen")
            return None
        out = self.next_swing_dir()
        out.mkdir()
        impact_wall = time.time() - (time.perf_counter() - t_impact)
        meta = {"recorded_at": datetime.now().isoformat(timespec="seconds"),
                "impact_wallclock": datetime.fromtimestamp(impact_wall).isoformat(
                    timespec="milliseconds"),
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
        return out


def run_cli(cfg: dict, session_dir: Path, preview: bool = False) -> list[Path]:
    """Konsolen-Modus: Enter = manuell speichern, q = beenden."""
    saved: list[Path] = []
    pool = None
    if cfg["capture"]["auto_analyze"]:
        from concurrent.futures import ProcessPoolExecutor
        pool = ProcessPoolExecutor(max_workers=1)

    def on_swing(p: Path):
        saved.append(p)
        print(f"  ✓ {p}", flush=True)
        if pool:
            from .pipeline import analyze_swing_safe
            pool.submit(analyze_swing_safe, str(p), cfg.get("_config_path"))

    eng = CaptureEngine(cfg, session_dir, on_swing, lambda s: print("  " + s, flush=True))
    for v, s in eng.start().items():
        print(f"  {v.upper():5s}: {s}")
    eng.armed = True
    quit_ev = threading.Event()

    def stdin_loop():
        for line in sys.stdin:
            if line.strip().lower() in ("q", "quit", "exit"):
                quit_ev.set()
                return
            eng.trigger_manual()

    threading.Thread(target=stdin_loop, daemon=True).start()
    print("  Bereit. Enter = Swing manuell speichern, q + Enter = beenden\n")
    try:
        while not quit_ev.is_set():
            if preview:
                tiles = [cv2.resize(f, (640, int(640 * f.shape[0] / f.shape[1])))
                         for f in (eng.latest(v) for v in VIEWS) if f is not None]
                if tiles and len({t.shape for t in tiles}) == 1:
                    cv2.imshow("swinglab", np.hstack(tiles))
                if cv2.waitKey(30) & 0xFF == ord("q"):
                    break
            else:
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop()
        if pool:
            print("  Warte auf laufende Analysen …")
            pool.shutdown(wait=True)
        cv2.destroyAllWindows()
    return saved


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
