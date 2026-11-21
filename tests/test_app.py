"""Rauchtest der Oberfläche (offscreen) mit Demo-Daten."""
import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_main_window_tabs(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from swinglab.app import main as M
    from swinglab.config import load_config

    from .synth import make_swing
    from .test_e2e import _write_video

    sw = tmp_path / "data" / "sessions" / "2026-11-21_1830" / "swing_001"
    sw.mkdir(parents=True)
    for v, t in make_swing().items():
        _write_video(sw / f"{v}.mp4", t).save(sw / f"pose_{v}.npz")
    (sw / "meta.json").write_text(json.dumps({"recorded_at": "2026-11-21T18:30:07", "synced": True}))
    cfg = load_config(tmp_path / "none.toml")
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["audio"]["enabled"] = False
    for v in ("dtl", "fo"):
        cfg["cameras"][v]["index"] = str(sw / f"{v}.mp4")   # Video statt Kamera
    from swinglab.pipeline import analyze_swing
    analyze_swing(sw, cfg, render_video=False)

    app = QApplication.instance() or QApplication([])
    w = M.MainWindow(cfg)
    try:
        w.open_swing(sw)
        w.viewer.goto_phase("impact")
        assert w.viewer.lbl_pos.text().endswith("+0 ms zum Impact")
        w.tabs.setCurrentWidget(w.trends)
        w.tabs.setCurrentWidget(w.settings)
        c = w.settings.collect()
        assert c["cameras"]["fo"]["index"] == str(sw / "fo.mp4") and c["shots"]["port"] == 9210
    finally:
        w.close()
        app.processEvents()
