import json
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path

from swinglab.shots import OpenConnectServer, ShotMatcher, parse_open_connect, split_json_stream

MSG = {"DeviceID": "Mevo+", "Units": "Yards", "ShotNumber": 3, "APIversion": "1",
       "BallData": {"Speed": 112.3, "SpinAxis": -3.1, "TotalSpin": 6400, "HLA": 1.2, "VLA": 17.8,
                    "CarryDistance": 152.4},
       "ClubData": {"Speed": 83.0, "AngleOfAttack": -4.1, "FaceToTarget": 0.8, "Path": -2.3},
       "ShotDataOptions": {"ContainsBallData": True, "ContainsClubData": True}}


def test_parse_open_connect():
    s = parse_open_connect(MSG)
    assert s["ball_speed"] == 112.3 and s["club_path"] == -2.3 and s["carry"] == 152.4
    assert s["face_to_path"] == 3.1 and abs(s["smash"] - 1.353) < 1e-3
    assert parse_open_connect({"ShotDataOptions": {"IsHeartBeat": True}}) is None


def test_split_stream():
    a = json.dumps(MSG)
    msgs, rest = split_json_stream(a + a[:20])
    assert len(msgs) == 1 and rest == a[:20]
    msgs, rest = split_json_stream(rest + a[20:])
    assert len(msgs) == 1 and rest == ""


def test_matcher_both_orders():
    m = ShotMatcher(before_s=2, after_s=15)
    t0 = datetime.now()
    shot = {"received_at": (t0 + timedelta(seconds=2)).isoformat()}
    assert m.add_shot(shot) == []                       # Schuss vor dem Speichern
    assert m.add_swing(Path("s1"), t0) == [(Path("s1"), shot)]
    late = {"received_at": (t0 + timedelta(seconds=40)).isoformat()}
    assert m.add_shot(late) == []                       # passt zu keinem Swing


def test_server_roundtrip():
    got = []
    srv = OpenConnectServer(got.append, port=0)
    srv.start()
    assert srv.ready.wait(3)
    with socket.create_connection(("127.0.0.1", srv.port), timeout=3) as c:
        hb = {"ShotDataOptions": {"IsHeartBeat": True}}
        c.sendall((json.dumps(hb) + json.dumps(MSG)).encode())
        assert b"200" in c.recv(200)
        for _ in range(50):
            if got:
                break
            time.sleep(0.02)
    srv.stop()
    assert len(got) == 1 and got[0]["club_speed"] == 83.0
