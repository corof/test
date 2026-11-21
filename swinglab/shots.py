"""Schussdaten live empfangen und Swings zuordnen.

OpenConnectServer: spricht das GSPro-Open-Connect-Protokoll (JSON über TCP), das viele
Launch-Monitor-Connectoren verwenden. swinglab kann
  a) Endpunkt sein (Connector zeigt auf swinglab) oder
  b) als Relais dazwischen sitzen: Connector → swinglab → GSPro (forward_host/port),
     Antworten von GSPro werden an den Connector zurückgereicht.

ShotMatcher: ordnet eintreffende Schüsse dem zeitlich passenden Swing zu
(Schuss kommt typischerweise 1–4 s nach dem Impact).
"""
from __future__ import annotations

import json
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

BALL_MAP = {"Speed": "ball_speed", "VLA": "launch_angle", "HLA": "launch_direction",
            "TotalSpin": "spin_rate", "SpinAxis": "spin_axis", "BackSpin": "backspin",
            "SideSpin": "sidespin", "CarryDistance": "carry"}
CLUB_MAP = {"Speed": "club_speed", "AngleOfAttack": "attack_angle", "FaceToTarget": "face_angle",
            "Path": "club_path", "Loft": "dynamic_loft", "Lie": "lie"}


def parse_open_connect(msg: dict) -> dict | None:
    """Open-Connect-Nachricht → kanonischer Schuss (None für Heartbeats/Status)."""
    opts = msg.get("ShotDataOptions") or {}
    if opts.get("IsHeartBeat") or not (opts.get("ContainsBallData") or msg.get("BallData")):
        return None
    shot: dict = {"source": "open_connect", "units": {}}
    for block, mapping in (("BallData", BALL_MAP), ("ClubData", CLUB_MAP)):
        for k, v in (msg.get(block) or {}).items():
            if k in mapping and isinstance(v, (int, float)):
                shot[mapping[k]] = float(v)
    if "ball_speed" not in shot:
        return None
    units = (msg.get("Units") or "Yards").lower()
    shot["units"] = {"ball_speed": "mph", "club_speed": "mph",
                     "carry": "m" if units.startswith("met") else "yds", "spin_rate": "rpm",
                     **{k: "°" for k in ("launch_angle", "launch_direction", "spin_axis",
                                         "attack_angle", "face_angle", "club_path", "face_to_path")}}
    if shot.get("club_speed"):
        shot["smash"] = round(shot["ball_speed"] / shot["club_speed"], 3)
    if "face_angle" in shot and "club_path" in shot:
        shot["face_to_path"] = round(shot["face_angle"] - shot["club_path"], 2)
    shot["shot_no"] = msg.get("ShotNumber")
    return shot


def split_json_stream(buf: str) -> tuple[list[dict], str]:
    """Zerlegt aneinandergehängte JSON-Objekte; Rest bleibt im Puffer."""
    dec, out, i = json.JSONDecoder(), [], 0
    while True:
        while i < len(buf) and buf[i] in " \r\n\t":
            i += 1
        if i >= len(buf):
            return out, ""
        try:
            obj, j = dec.raw_decode(buf, i)
        except json.JSONDecodeError:
            return out, buf[i:]
        if isinstance(obj, dict):
            out.append(obj)
        i = j


class OpenConnectServer(threading.Thread):
    def __init__(self, on_shot: Callable[[dict], None], port: int = 921, host: str = "127.0.0.1",
                 forward: tuple[str, int] | None = None,
                 on_status: Callable[[str], None] | None = None):
        super().__init__(daemon=True, name="open-connect")
        self.on_shot, self.port, self.host, self.forward = on_shot, port, host, forward
        self.on_status = on_status or (lambda s: None)
        self._sock: socket.socket | None = None
        self._halt = threading.Event()
        self.ready = threading.Event()

    def run(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.host, self.port))
        except OSError as e:
            self.on_status(f"Open Connect: Port {self.port} belegt ({e})")
            self.ready.set()
            return
        self.port = self._sock.getsockname()[1]
        self._sock.listen(2)
        self._sock.settimeout(0.5)
        self.ready.set()
        self.on_status(f"Open Connect wartet auf Port {self.port}")
        while not self._halt.is_set():
            try:
                conn, addr = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._serve_client, args=(conn, addr), daemon=True).start()

    def _serve_client(self, conn: socket.socket, addr):
        self.on_status(f"Launch Monitor verbunden ({addr[0]})")
        upstream = None
        if self.forward:
            try:
                upstream = socket.create_connection(self.forward, timeout=3)
                threading.Thread(target=self._pipe_back, args=(upstream, conn), daemon=True).start()
            except OSError as e:
                self.on_status(f"Weiterleitung an {self.forward} fehlgeschlagen: {e}")
                upstream = None
        buf = ""
        conn.settimeout(1.0)
        with conn:
            while not self._halt.is_set():
                try:
                    data = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                if upstream:
                    try:
                        upstream.sendall(data)
                    except OSError:
                        upstream = None
                msgs, buf = split_json_stream(buf + data.decode("utf-8", "replace"))
                for m in msgs:
                    shot = parse_open_connect(m)
                    if shot:
                        shot["received_at"] = datetime.now().isoformat(timespec="milliseconds")
                        self.on_shot(shot)
                    if not upstream:  # selbst antworten, wenn kein GSPro dahinter
                        try:
                            conn.sendall(json.dumps({"Code": 200, "Message": "Shot received"}).encode())
                        except OSError:
                            pass
        if upstream:
            upstream.close()
        self.on_status("Launch Monitor getrennt")

    @staticmethod
    def _pipe_back(src: socket.socket, dst: socket.socket):
        try:
            while True:
                d = src.recv(65536)
                if not d:
                    break
                dst.sendall(d)
        except OSError:
            pass

    def stop(self):
        self._halt.set()
        if self._sock:
            self._sock.close()


class ShotMatcher:
    """Ordnet Schüsse Swings zu: Schuss-Eingang im Fenster [Impact − before, Impact + after]."""

    def __init__(self, before_s: float = 2.0, after_s: float = 15.0):
        self.before, self.after = before_s, after_s
        self.swings: list[tuple[datetime, Path]] = []
        self.shots: list[dict] = []
        self.done: set[Path] = set()

    def add_swing(self, swing_dir: Path, impact: datetime) -> list[tuple[Path, dict]]:
        self.swings.append((impact, swing_dir))
        return self._match()

    def add_shot(self, shot: dict) -> list[tuple[Path, dict]]:
        self.shots.append(shot)
        return self._match()

    def _match(self) -> list[tuple[Path, dict]]:
        pairs = []
        for shot in list(self.shots):
            t = datetime.fromisoformat(shot["received_at"])
            best = None
            for imp, d in self.swings:
                if d in self.done:
                    continue
                dt = (t - imp).total_seconds()
                if -self.before <= dt <= self.after and (best is None or dt < best[0]):
                    best = (dt, d)
            if best:
                self.done.add(best[1])
                self.shots.remove(shot)
                pairs.append((best[1], shot))
        # alte, unzuordenbare Schüsse verwerfen
        now = datetime.now()
        self.shots = [s for s in self.shots
                      if (now - datetime.fromisoformat(s["received_at"])).total_seconds() < 60]
        return pairs


def write_shot(swing_dir: Path, shot: dict):
    (Path(swing_dir) / "mevo.json").write_text(json.dumps(shot, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
