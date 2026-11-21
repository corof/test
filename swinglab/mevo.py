"""Import von Mevo+-Schussdaten (CSV-Export aus der FS Golf App) und Zuordnung zu Swings.

Der Parser ist bewusst tolerant: Spaltennamen werden normalisiert (Einheiten in
Klammern entfernt, Gross-/Kleinschreibung egal), Richtungsangaben wie "3.2 L" /
"R 1.5" werden zu −3.2 / +1.5 (L = links = negativ), Dezimalkomma wird erkannt.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

ALIASES = {
    "club_speed": ["clubspeed", "clubheadspeed", "schlägerkopfgeschwindigkeit", "schlaegergeschwindigkeit"],
    "ball_speed": ["ballspeed", "ballgeschwindigkeit"],
    "smash": ["smashfactor", "smash"],
    "launch_angle": ["launchangle", "verticallaunch", "launchv", "abflugwinkel", "vla"],
    "launch_direction": ["launchdirection", "horizontallaunch", "launchh", "abflugrichtung", "hla"],
    "spin_rate": ["spinrate", "totalspin", "spin", "gesamtspin"],
    "spin_axis": ["spinaxis", "spinachse"],
    "backspin": ["backspin"],
    "sidespin": ["sidespin"],
    "attack_angle": ["angleofattack", "attackangle", "aoa", "anstellwinkel"],
    "club_path": ["clubpath", "path", "schwungbahn"],
    "face_angle": ["clubface", "faceangle", "face", "facetotarget", "schlagfläche"],
    "face_to_path": ["facetopath"],
    "carry": ["carrydistance", "carry"],
    "total": ["totaldistance", "total", "gesamtdistanz"],
    "offline": ["carrydeviationdistance", "offline", "lateral", "carrylateral", "sidecarry",
                "abweichung"],
    "offline_angle": ["carrydeviationangle"],
    "apex": ["apexheight", "apex", "peakheight", "height", "maxheight"],
    "flight_time": ["flighttime"],
    "club": ["clubname", "club", "clubtype", "schläger"],
    "date": ["date", "datetime", "timestamp", "datum", "time", "zeit", "shottime"],
    "shot_no": ["shot", "shotnumber", "no", "nr"],
}
TEXT_FIELDS = {"club", "date"}
DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S", "%m/%d/%y %I:%M %p", "%m/%d/%y %H:%M"]

_num = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def norm_header(h: str) -> tuple[str, str]:
    """→ (normalisierter Name, Einheit)"""
    unit = ""
    m = re.search(r"[\(\[]([^\)\]]*)[\)\]]", h)
    if m:
        unit = m.group(1).strip()
    base = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", h).lower()
    return re.sub(r"[^a-zäöü]", "", base), unit


def canonical(h: str) -> str | None:
    n, _ = norm_header(h)
    for key, names in ALIASES.items():
        if n in names:
            return key
    return None


def parse_value(s: str) -> float | None:
    s = (s or "").strip()
    if not s or s in ("-", "--", "N/A", "n/a"):
        return None
    m = _num.search(s)
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    rest = (s[:m.start()] + s[m.end():]).strip().upper()
    if re.fullmatch(r"(L|LEFT|LINKS)\b.*|.*\b(L|LEFT|LINKS)", rest):
        v = -abs(v)
    elif re.fullmatch(r"(R|RIGHT|RECHTS)\b.*|.*\b(R|RIGHT|RECHTS)", rest):
        v = abs(v)
    return v


def parse_date(s: str) -> datetime | None:
    s = (s or "").strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def read_csv(path: str | Path) -> tuple[list[dict], dict[str, str]]:
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV-Kodierung nicht erkannt")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    # Kopfzeile = erste Zeile mit ≥3 erkannten Spalten
    hi = next((i for i, r in enumerate(rows) if sum(canonical(c) is not None for c in r) >= 3), None)
    if hi is None:
        raise ValueError("Keine Mevo-/FS-Golf-Kopfzeile gefunden")
    header = rows[hi]
    cols = {i: canonical(c) for i, c in enumerate(header)}
    units = {cols[i]: norm_header(c)[1] for i, c in enumerate(header) if cols[i]}
    body = rows[hi + 1:]
    # optionale Einheitenzeile direkt unter dem Header
    if body and sum(bool(re.fullmatch(r"\s*[\(\[]?[a-zA-Z°/%]+[\)\]]?\s*", c)) for c in body[0]) \
            >= max(3, len(body[0]) // 2):
        for i, c in enumerate(body[0]):
            if cols.get(i) and not units.get(cols[i]):
                units[cols[i]] = c.strip("()[] ")
        body = body[1:]
    shots = []
    for r in body:
        if not any(c.strip() for c in r):
            continue
        shot: dict = {}
        for i, c in enumerate(r):
            k = cols.get(i)
            if not k or k in shot:
                continue
            shot[k] = c.strip() if k in TEXT_FIELDS else parse_value(c)
        nums = [v for k, v in shot.items() if k not in TEXT_FIELDS and v is not None]
        if len(nums) < 2:  # Zusammenfassungs-/Leerzeilen (Average etc.) überspringen
            continue
        if any(str(c).strip().lower() in ("average", "avg", "mittelwert", "std", "stdev") for c in r[:3]):
            continue
        if shot.get("date"):
            d = parse_date(shot["date"])
            shot["datetime"] = d.isoformat() if d else None
        shots.append(shot)
    return shots, units


def match_shots(swings: list[dict], shots: list[dict], tolerance_s: float = 30,
                offset: int = 0) -> list[tuple[int, int]]:
    """swings: [{'recorded_at': iso}], shots aus read_csv. → [(swing_idx, shot_idx)]"""
    have_time = all(s.get("datetime") for s in shots) and all(w.get("recorded_at") for w in swings)
    if have_time and offset == 0:
        cands = []
        for i, w in enumerate(swings):
            tw = datetime.fromisoformat(w["recorded_at"])
            for j, s in enumerate(shots):
                dt = abs((datetime.fromisoformat(s["datetime"]) - tw).total_seconds())
                if dt <= tolerance_s:
                    cands.append((dt, i, j))
        used_i, used_j, pairs = set(), set(), []
        for _, i, j in sorted(cands):
            if i not in used_i and j not in used_j:
                pairs.append((i, j))
                used_i.add(i)
                used_j.add(j)
        if pairs:
            return sorted(pairs)
    # Fallback: Reihenfolge (mit optionalem Versatz)
    return [(i, i + offset) for i in range(len(swings)) if 0 <= i + offset < len(shots)]


def import_into_session(session_dir: Path, csv_path: Path, tolerance_s: float = 30,
                        offset: int = 0) -> list[tuple[str, int]]:
    shots, units = read_csv(csv_path)
    swing_dirs = sorted(p for p in session_dir.glob("swing_*") if p.is_dir())
    swings = []
    for d in swing_dirs:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
        swings.append(meta)
    pairs = match_shots(swings, shots, tolerance_s, offset)
    out = []
    for i, j in pairs:
        data = {"shot_index": j, "units": units, **shots[j]}
        (swing_dirs[i] / "mevo.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
        out.append((swing_dirs[i].name, j))
    return out
