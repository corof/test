import json

import numpy as np
import pytest

from swinglab.audio import LiveDetectorConfig, LiveImpactDetector, detect_impacts
from swinglab.capture import resample_to_timeline
from swinglab.config import set_value_in_toml
from swinglab.mevo import match_shots, parse_value, read_csv

SR = 48000


def _impact_audio(times, dur=4.0, seed=1):
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.003, int(dur * SR))
    for t in times:
        i = int(t * SR)
        k = np.arange(int(0.01 * SR))
        a[i:i + len(k)] += 0.6 * np.exp(-k / (0.0015 * SR)) * rng.choice([-1, 1], len(k))
        # Ball trifft Leinwand ~90 ms später
        j = i + int(0.09 * SR)
        a[j:j + len(k)] += 0.4 * np.exp(-k / (0.002 * SR)) * rng.choice([-1, 1], len(k))
    return a


def test_offline_impact_takes_first_peak():
    hits = detect_impacts(_impact_audio([1.2, 3.0]), SR, refractory_s=1.0)
    assert len(hits) == 2
    assert hits[0] == pytest.approx(1.2, abs=0.003)
    assert hits[1] == pytest.approx(3.0, abs=0.003)


def test_live_detector_blocks():
    a = _impact_audio([1.5])
    det = LiveImpactDetector(LiveDetectorConfig(), lambda t: None)
    hits = []
    for s in range(0, len(a), 512):
        blk = a[s:s + 512]
        h = det.process_block(blk, (s + len(blk)) / SR)
        if h is not None:
            hits.append(h)
    assert len(hits) == 1 and hits[0] == pytest.approx(1.5, abs=0.003)


def test_resample_nearest():
    frames = [(t, None) for t in np.arange(0, 1, 1 / 59.3)]  # leicht unregelmässig
    tl = np.arange(0.1, 0.9, 1 / 60)
    idx = resample_to_timeline(frames, tl)
    ts = np.array([f[0] for f in frames])
    assert np.max(np.abs(ts[idx] - tl)) <= 0.5 / 59.3 + 1e-9


@pytest.mark.parametrize("s,v", [("3.2 L", -3.2), ("R 1.5", 1.5), ("-2,4", -2.4), ("95.1 mph", 95.1),
                                 ("", None), ("--", None), ("1.2 Right", 1.2)])
def test_parse_value(s, v):
    assert parse_value(s) == (pytest.approx(v) if v is not None else None)


FS_CSV = """Player,Mevo Session
Date,Club Name,Club Speed (mph),Ball Speed (mph),Smash Factor,Launch Angle (deg),Launch Direction (deg),Spin Rate (rpm),Spin Axis (deg),Angle of Attack (deg),Club Path (deg),Club Face (deg),Carry Distance (yds),Total Distance (yds),Carry Deviation Distance (yds)
11/21/2026 18:30:05,7 Iron,82.1,111.0,1.35,18.2,1.2 R,6800,3.1 R,-4.2,2.1 L,0.5 R,152.3,160.1,5.2 R
11/21/2026 18:31:10,7 Iron,83.0,113.5,1.37,17.5,0.4 L,6500,1.0 L,-3.8,1.1 L,1.0 L,155.0,163.2,2.0 L
Average,,82.5,112.2,1.36,17.8,,,,,,,153.6,,
"""


def test_read_fs_golf_csv(tmp_path):
    p = tmp_path / "fs.csv"
    p.write_text(FS_CSV)
    shots, units = read_csv(p)
    assert len(shots) == 2
    s = shots[0]
    assert s["club_speed"] == 82.1 and s["club_path"] == -2.1 and s["face_angle"] == 0.5
    assert s["offline"] == 5.2 and s["attack_angle"] == -4.2 and s["club"] == "7 Iron"
    assert s["datetime"] == "2026-11-21T18:30:05"
    assert units["club_speed"] == "mph"


def test_read_semicolon_decimal_comma(tmp_path):
    p = tmp_path / "de.csv"
    p.write_text("Datum;Schläger;Club Speed;Ball Speed;Carry\n21.11.2026 18:30:05;7i;82,1;111,0;139,2\n",
                 encoding="cp1252")
    shots, _ = read_csv(p)
    assert shots[0]["club_speed"] == 82.1 and shots[0]["carry"] == 139.2


def test_match_by_time_and_order():
    swings = [{"recorded_at": "2026-11-21T18:30:07"}, {"recorded_at": "2026-11-21T18:31:12"}]
    shots = [{"datetime": "2026-11-21T18:29:00"}, {"datetime": "2026-11-21T18:30:05"},
             {"datetime": "2026-11-21T18:31:10"}]
    assert match_shots(swings, shots, 30) == [(0, 1), (1, 2)]
    assert match_shots(swings, [{}, {}, {}], 30, offset=1) == [(0, 1), (1, 2)]


def test_set_value_in_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[cameras.fo]\nindex = 1\npx_per_cm = 0.0   # Kommentar\n\n[audio]\nx = 1\n")
    set_value_in_toml(p, "cameras.fo", "px_per_cm", 3.21)
    set_value_in_toml(p, "audio", "device", "Kiyo")
    import tomllib
    d = tomllib.loads(p.read_text())
    assert d["cameras"]["fo"]["px_per_cm"] == 3.21 and d["audio"]["device"] == "Kiyo"
