import numpy as np
import pytest

from swinglab.analysis import analyze_tracks, detect_phases
from swinglab.config import load_config
from swinglab.feedback import evaluate
from swinglab.pose import clean

from .synth import FPS, T_ADDR, T_IMP, T_TOP, make_swing


@pytest.fixture
def cfg():
    c = load_config("/nonexistent.toml")
    c["golfer"]["body_height_cm"] = 180
    return c


def test_phases_found():
    tr = make_swing()
    ph = detect_phases(tr["fo"])
    assert abs(ph["top"] - T_TOP * FPS) <= 2
    assert abs(ph["impact"] - T_IMP * FPS) <= 2
    assert abs(ph["address"] - T_ADDR * FPS) <= 4
    assert ph["address"] < ph["takeaway"] < ph["top"] < ph["downswing"] < ph["impact"] < ph["finish"]


def test_phases_with_audio_hint_refines():
    tr = make_swing()
    hint = int(T_IMP * FPS) + 3  # Audio leicht daneben
    ph = detect_phases(tr["fo"], hint)
    assert abs(ph["impact"] - T_IMP * FPS) <= 1.5


@pytest.mark.parametrize("flip", [False, True])
def test_metrics_signs_and_scale(cfg, flip):
    tr = make_swing(view_flip_fo=flip)
    res = analyze_tracks(tr, cfg, synced=True)
    m = res["metrics"]
    assert m["hip_sway_top_cm"] == pytest.approx(-10, abs=1.0)
    assert m["hip_slide_impact_cm"] == pytest.approx(6, abs=1.0)
    assert m["head_sway_impact_cm"] == pytest.approx(2, abs=1.0)
    assert m["early_extension_cm"] == pytest.approx(7, abs=1.0)
    assert m["spine_angle_change_impact_deg"] < -2
    assert m["tempo_ratio"] == pytest.approx((T_TOP - T_ADDR) / (T_IMP - T_TOP), rel=0.15)
    assert m["shoulder_turn_top_deg"] == pytest.approx(90, abs=3)
    assert m["hip_turn_top_deg"] == pytest.approx(45, abs=3)
    assert m["x_factor_top_deg"] == pytest.approx(45, abs=4)
    assert m["hip_open_impact_deg"] == pytest.approx(40, abs=4)
    assert m["lead_elbow_top_deg"] == pytest.approx(180, abs=2)


def test_feedback_prioritises(cfg):
    tr = make_swing(hip_sway_top_cm=-14, early_ext_cm=9)
    m = analyze_tracks(tr, cfg, synced=True)["metrics"]
    keys = [f.key for f in evaluate(m, cfg["thresholds"])]
    assert "hip_sway_top" in keys and "early_extension" in keys
    assert "head_sway_impact" not in keys


def test_clean_fills_gaps_and_survives_noise():
    tr = make_swing()["fo"]
    rng = np.random.default_rng(0)
    tr.px += rng.normal(0, 2, tr.px.shape)
    tr.vis[100:106, :] = 0.0  # kurz verdeckt
    c = clean(tr)
    assert np.isfinite(c.px[100:106, 23]).all()
    ph = detect_phases(c)
    assert abs(ph["impact"] - T_IMP * FPS) <= 3
