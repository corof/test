import json

import cv2
import numpy as np

from swinglab.config import load_config
from swinglab.overlay import draw_pose
from swinglab.pipeline import analyze_swing
from swinglab.report import build

from .synth import make_swing


def _write_video(path, track):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), track.fps, (960, 540))
    half = type(track)(track.px / 2, track.vis, track.world, track.fps, (960, 540))
    for i in range(track.n):
        img = np.full((540, 960, 3), 40, np.uint8)
        draw_pose(img, half, i, 2)
        vw.write(img)
    vw.release()
    return half


def test_end_to_end(tmp_path):
    cfg = load_config("/nonexistent.toml")
    cfg["paths"]["data_dir"] = str(tmp_path)
    swing = tmp_path / "sessions" / "2026-11-21_1830" / "swing_001"
    swing.mkdir(parents=True)
    tracks = make_swing()
    for v, t in tracks.items():
        half = _write_video(swing / f"{v}.mp4", t)
        half.save(swing / f"pose_{v}.npz")   # Pose-Cache -> MediaPipe wird übersprungen
    (swing / "meta.json").write_text(json.dumps({"recorded_at": "2026-11-21T18:30:07",
                                                 "synced": True, "impact_time_s": None}))
    (swing / "mevo.json").write_text(json.dumps({"carry": 150.2, "club_path": -2.1,
                                                 "units": {"carry": "yds"}}))
    res = analyze_swing(swing, cfg)
    assert res["metrics"]["early_extension_cm"] > 5
    assert (swing / "keyframes.jpg").exists() and (swing / "overlay.mp4").exists()
    kf = cv2.imread(str(swing / "keyframes.jpg"))
    assert kf.shape[0] == 2 * 420 and kf.shape[1] == 6 * 300
    out = build(tmp_path)
    html = out.read_text()
    assert "Early Extension" in html and "keyframes.jpg" in html and "150.2" in html
