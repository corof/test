"""Analyse eines Swing-Ordners: Pose → Phasen → Kennzahlen → Feedback → Overlay."""
from __future__ import annotations

import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path

from .analysis import analyze_tracks
from .config import load_config
from .feedback import evaluate
from .pose import PoseTrack, clean, ensure_model, estimate_pose

VIEWS = ("dtl", "fo")


def _meta(swing_dir: Path) -> dict:
    p = swing_dir / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def analyze_swing(swing_dir: str | Path, cfg: dict, force: bool = False,
                  render_video: bool = True) -> dict:
    swing_dir = Path(swing_dir)
    meta = _meta(swing_dir)
    acfg = cfg["analysis"]
    model = None
    tracks: dict[str, PoseTrack] = {}
    for v in VIEWS:
        video = swing_dir / f"{v}.mp4"
        if not video.exists():
            continue
        cache = swing_dir / f"pose_{v}.npz"
        if cache.exists() and not force:
            raw = PoseTrack.load(cache)
        else:
            model = model or ensure_model(cfg["paths"]["model_dir"], acfg["model"])
            raw = estimate_pose(video, model)
            raw.save(cache)
        tracks[v] = clean(raw, acfg["min_visibility"], acfg["smooth_window_s"],
                          acfg["max_gap_frames"])
    if not tracks:
        raise FileNotFoundError(f"Keine Videos (dtl.mp4 / fo.mp4) in {swing_dir}")

    hints = {v: meta.get("impact_time_s") for v in tracks}
    hints.update({v: t for v, t in (meta.get("impact_by_view") or {}).items() if t is not None})
    res = analyze_tracks(tracks, cfg, hints, bool(meta.get("synced")))
    findings = evaluate(res["metrics"], cfg["thresholds"])
    res["findings"] = [f.as_dict() for f in findings]
    res["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
    res["fps"] = {v: t.fps for v, t in tracks.items()}
    if render_video and res["phases"]:
        from .overlay import render
        res["media"] = render(swing_dir, tracks, res["phases"])
    (swing_dir / "analysis.json").write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                             encoding="utf-8")
    return res


def analyze_swing_safe(swing_dir: str, config_path: str | None = None) -> None:
    """Für den Hintergrund-Prozess während der Aufnahme."""
    try:
        cfg = load_config(config_path)
        res = analyze_swing(swing_dir, cfg)
        top = res["findings"][0]["title"] if res["findings"] else "keine Auffälligkeiten"
        m = res["metrics"]
        print(f"  ▶ {Path(swing_dir).name}: Tempo {m.get('tempo_ratio')}:1 – {top}", flush=True)
    except Exception:  # noqa: BLE001
        print(f"  ! Analyse fehlgeschlagen für {swing_dir}:\n{traceback.format_exc()}", flush=True)


def import_clips(cfg: dict, session_dir: Path, dtl: str | None, fo: str | None,
                 synced: bool = False) -> Path:
    """Externe Videos (z.B. Handy/OBS) als Swing übernehmen."""
    from .audio import audio_from_video, detect_impacts
    from .pose import video_info

    session_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(session_dir.glob("swing_*"))) + 1
    out = session_dir / f"swing_{n:03d}"
    out.mkdir()
    meta = {"recorded_at": datetime.now().isoformat(timespec="seconds"), "trigger": "import",
            "synced": synced, "impact_time_s": None, "cameras": {}}
    for v, src in (("dtl", dtl), ("fo", fo)):
        if not src:
            continue
        dst = out / f"{v}.mp4"
        if Path(src).suffix.lower() == ".mp4":
            shutil.copy2(src, dst)
        else:
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-an", "-c:v", "libx264",
                            "-crf", "18", str(dst)], check=True)
        fps, frames, size = video_info(dst)
        meta["cameras"][v] = {"source": str(src), "fps": fps, "frames": frames, "size": size}
        a = audio_from_video(src)
        if a is not None:
            ac = cfg["audio"]
            hits = detect_impacts(a, 48000, ac["highpass_hz"], ac["threshold_factor"], ac["min_peak"])
            if hits:
                meta.setdefault("impact_by_view", {})[v] = round(hits[0], 4)
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out
