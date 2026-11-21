"""Swing-Phasen und biomechanische Kennzahlen aus Pose-Tracks.

Vorzeichen-Konventionen (Rechtshänder wie Linkshänder gleich):
  Face-On (fo):   + = Richtung Ziel,  − = vom Ziel weg
  Down-the-Line:  + = Richtung Ball (zum Ball hin), − = vom Ball weg
  vertikal:       + = nach oben
  Rotation:       + = in Rückschwung-Richtung (bei Impact negativ = offen)
"""
from __future__ import annotations

import math

import numpy as np

from .pose import (L_ANK, L_EAR, L_EL, L_HIP, L_KNEE, L_SH, L_WR, NOSE, R_ANK, R_EAR, R_EL,
                   R_HIP, R_KNEE, R_SH, R_WR, PoseTrack)

PHASES = ["address", "takeaway", "top", "downswing", "impact", "finish"]
PHASE_LABELS = {"address": "Address", "takeaway": "Takeaway", "top": "Top",
                "downswing": "Downswing", "impact": "Impact", "finish": "Finish"}
LEG_RATIO = 0.49  # Oberschenkel + Unterschenkel ≈ 0.49 × Körpergrösse (Drillis/Contini)


def _mid(a: np.ndarray, i: int, j: int) -> np.ndarray:
    with np.errstate(all="ignore"):
        return np.nanmean(np.stack([a[:, i], a[:, j]]), axis=0)


def hands(t: PoseTrack) -> np.ndarray:
    return _mid(t.px, L_WR, R_WR)


def pelvis(t: PoseTrack) -> np.ndarray:
    return _mid(t.px, L_HIP, R_HIP)


def chest(t: PoseTrack) -> np.ndarray:
    return _mid(t.px, L_SH, R_SH)


def head(t: PoseTrack) -> np.ndarray:
    with np.errstate(all="ignore"):
        return np.nanmean(np.stack([t.px[:, NOSE], t.px[:, L_EAR], t.px[:, R_EAR]]), axis=0)


def leg_length_px(t: PoseTrack, frame: int, span: int = 3) -> float:
    s = slice(max(0, frame - span), frame + span + 1)
    p = t.px[s]
    legs = []
    for hip, knee, ank in ((L_HIP, L_KNEE, L_ANK), (R_HIP, R_KNEE, R_ANK)):
        legs.append(np.linalg.norm(p[:, hip] - p[:, knee], axis=-1)
                    + np.linalg.norm(p[:, knee] - p[:, ank], axis=-1))
    v = np.nanmedian(np.concatenate(legs)) if np.isfinite(np.concatenate(legs)).any() else np.nan
    return float(v)


def _nan_argmin(x: np.ndarray, lo: int, hi: int) -> int | None:
    lo, hi = max(0, lo), min(len(x), hi)
    if hi <= lo or not np.isfinite(x[lo:hi]).any():
        return None
    return lo + int(np.nanargmin(x[lo:hi]))


def detect_phases(t: PoseTrack, impact_hint: int | None = None) -> dict[str, int]:
    """Erkennt die Schlüsselpositionen über die Handbahn (Mitte der Handgelenke)."""
    fps, n = t.fps, t.n
    h, p = hands(t), pelvis(t)
    if not np.isfinite(h).any():
        raise ValueError("Keine Hände erkannt – ist der Golfer vollständig im Bild?")
    hy, py = h[:, 1], p[:, 1]
    with np.errstate(all="ignore"):
        speed = np.r_[0, np.linalg.norm(np.diff(h, axis=0), axis=1)] * fps
    vmax = float(np.nanmax(speed))
    s = int(round(fps))

    if impact_hint is not None and 0 <= impact_hint < n:
        top = _nan_argmin(hy, impact_hint - 2 * s, impact_hint)
    else:
        top = _nan_argmin(hy, int(np.nanargmax(speed)) - 2 * s, int(np.nanargmax(speed)) + 1)
    if top is None:
        raise ValueError("Top-Position nicht gefunden")

    # Address: letzter ruhiger Abschnitt (≥0.2 s) vor dem Top
    thr, need = 0.06 * vmax, max(3, int(0.2 * fps))
    still = np.nan_to_num(speed, nan=np.inf) < thr
    address, run = 0, 0
    for i in range(top, -1, -1):
        run = run + 1 if still[i] else 0
        if run >= need:
            address = i + need - 1
            break
    h_addr = np.nanmedian(h[max(0, address - need):address + 1], axis=0)
    dist = np.linalg.norm(h - h_addr, axis=1)

    # Impact: Hände zurück nahe der Address-Position, unterhalb der Hüfte
    if impact_hint is not None and 0 <= impact_hint < n:
        r = max(2, int(0.08 * fps))
        impact = _nan_argmin(dist, impact_hint - r, impact_hint + r + 1)
        impact = impact_hint if impact is None else impact
    else:
        is_below = (hy > py) & (np.arange(n) > top)
        below = np.flatnonzero(is_below)
        impact = None
        if below.size:
            seg_end = below[0]
            while seg_end + 1 < n and is_below[seg_end + 1]:
                seg_end += 1
            impact = _nan_argmin(dist, below[0], seg_end + 1)
        if impact is None:
            impact = min(n - 1, top + int(0.3 * fps))

    def first(cond, lo, hi, default):
        idx = np.flatnonzero(cond[lo:hi])
        return lo + int(idx[0]) if idx.size else default

    takeaway = first(hy < py, address + 1, top, (address + top) // 2)
    downswing = first(hy > py, top + 1, impact + 1, (top + impact) // 2)
    finish = _nan_argmin(hy, impact + 1, impact + int(1.5 * fps))
    finish = min(n - 1, impact + 1) if finish is None else finish
    ph = dict(address=address, takeaway=takeaway, top=top, downswing=downswing,
              impact=impact, finish=finish)
    # Monotonie erzwingen
    prev = -1
    for k in PHASES:
        ph[k] = int(min(max(ph[k], prev + 1), n - 1))
        prev = ph[k]
    return ph


def _angle(a, b, c) -> float:
    """Winkel bei b (Grad) zwischen a-b-c; funktioniert für 2D und 3D."""
    v1, v2 = np.asarray(a) - b, np.asarray(c) - b
    den = np.linalg.norm(v1) * np.linalg.norm(v2)
    if not np.isfinite(den) or den == 0:
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / den, -1, 1))))


def scale_px_per_cm(t: PoseTrack, address: int, calibrated: float, height_cm: float) -> tuple[float, str]:
    if calibrated and calibrated > 0:
        return float(calibrated), "kalibriert"
    leg = leg_length_px(t, address)
    if not np.isfinite(leg) or leg <= 0:
        return float("nan"), "unbekannt"
    return leg / (LEG_RATIO * height_cm), "geschätzt (Körpergrösse)"


def _r(x, nd=1):
    return None if x is None or not np.isfinite(x) else round(float(x), nd) + 0.0


def face_on_metrics(t: PoseTrack, ph: dict, ppc: float) -> dict:
    a, top, imp = ph["address"], ph["top"], ph["impact"]
    hd, pv, hn = head(t), pelvis(t), hands(t)
    # Zielrichtung: Hände bewegen sich im Rückschwung vom Ziel weg
    tgt = -np.sign(hn[top, 0] - hn[a, 0]) or 1.0
    dx = lambda arr, i: tgt * (arr[i, 0] - arr[a, 0]) / ppc  # noqa: E731
    dy = lambda arr, i: -(arr[i, 1] - arr[a, 1]) / ppc        # noqa: E731
    return {
        "fo_target_sign": tgt,
        "head_sway_top_cm": _r(dx(hd, top)),
        "head_sway_impact_cm": _r(dx(hd, imp)),
        "head_vertical_impact_cm": _r(dy(hd, imp)),
        "hip_sway_top_cm": _r(dx(pv, top)),
        "hip_slide_impact_cm": _r(dx(pv, imp)),
        "hands_ahead_impact_cm": _r(tgt * (hn[imp, 0] - pv[imp, 0]) / ppc
                                    - tgt * (hn[a, 0] - pv[a, 0]) / ppc),
    }


def dtl_metrics(t: PoseTrack, ph: dict, ppc: float, handed: str) -> dict:
    a, top, imp = ph["address"], ph["top"], ph["impact"]
    pv, ch, hd, hn = pelvis(t), chest(t), head(t), hands(t)
    ball = np.sign(hn[a, 0] - pv[a, 0]) or 1.0

    def spine(i):
        v = ch[i] - pv[i]
        return math.degrees(math.atan2(ball * v[0], -v[1]))

    trail = (R_HIP, R_KNEE, R_ANK) if handed == "right" else (L_HIP, L_KNEE, L_ANK)
    knee = lambda i: _angle(t.px[i, trail[0]], t.px[i, trail[1]], t.px[i, trail[2]])  # noqa: E731
    sp_a = spine(a)
    return {
        "dtl_ball_sign": ball,
        "spine_angle_address_deg": _r(sp_a),
        "spine_angle_change_top_deg": _r(spine(top) - sp_a),
        "spine_angle_change_impact_deg": _r(spine(imp) - sp_a),
        "early_extension_cm": _r(ball * (pv[imp, 0] - pv[a, 0]) / ppc),
        "hip_depth_top_cm": _r(ball * (pv[top, 0] - pv[a, 0]) / ppc),
        "head_vertical_impact_dtl_cm": _r(-(hd[imp, 1] - hd[a, 1]) / ppc),
        "trail_knee_address_deg": _r(knee(a)),
        "trail_knee_change_top_deg": _r(knee(top) - knee(a)),
    }


def rotation_metrics(t: PoseTrack, ph: dict, handed: str) -> dict:
    """3D-Rotation aus MediaPipe-Weltkoordinaten (Schätzung, ±10°)."""
    w = t.world
    lead_sh, trail_sh = (L_SH, R_SH) if handed == "right" else (R_SH, L_SH)
    lead_hip, trail_hip = (L_HIP, R_HIP) if handed == "right" else (R_HIP, L_HIP)
    a, top, imp = ph["address"], ph["top"], ph["impact"]

    def yaw(i, j):
        v = w[:, i] - w[:, j]
        y = np.arctan2(v[:, 2], v[:, 0])
        ok = np.isfinite(y)
        if ok.sum() < 3:
            return None
        y[ok] = np.unwrap(y[ok])
        return np.degrees(y)

    sh, hp = yaw(lead_sh, trail_sh), yaw(lead_hip, trail_hip)
    if sh is None or hp is None:
        return {}
    sh, hp = sh - sh[a], hp - hp[a]
    sgn = np.sign(sh[top]) or 1.0
    sh, hp = sgn * sh, sgn * hp
    out = {
        "shoulder_turn_top_deg": _r(sh[top], 0),
        "hip_turn_top_deg": _r(hp[top], 0),
        "x_factor_top_deg": _r(sh[top] - hp[top], 0),
        "hip_open_impact_deg": _r(-hp[imp], 0),
        "shoulder_open_impact_deg": _r(-sh[imp], 0),
    }
    # Kinematische Sequenz: Becken sollte vor den Schultern maximal rotieren
    lo, hi = top, min(t.n, imp + max(2, int(0.05 * t.fps)))
    if hi - lo >= 4:
        vh, vs = -np.gradient(hp[lo:hi]), -np.gradient(sh[lo:hi])
        if np.isfinite(vh).any() and np.isfinite(vs).any():
            out["pelvis_lead_ms"] = _r((np.nanargmax(vs) - np.nanargmax(vh)) * 1000 / t.fps, 0)
    lead_arm = (L_SH, L_EL, L_WR) if handed == "right" else (R_SH, R_EL, R_WR)
    out["lead_elbow_top_deg"] = _r(_angle(w[top, lead_arm[0]], w[top, lead_arm[1]],
                                          w[top, lead_arm[2]]), 0)
    return out


def tempo_metrics(ph: dict, fps: float) -> dict:
    bs = (ph["top"] - ph["address"]) / fps
    ds = (ph["impact"] - ph["top"]) / fps
    return {"backswing_s": _r(bs, 2), "downswing_s": _r(ds, 2),
            "tempo_ratio": _r(bs / ds if ds > 0 else float("nan"), 2)}


def analyze_tracks(tracks: dict[str, PoseTrack], cfg: dict,
                   impact_hints_s: dict[str, float | None] | None = None,
                   synced: bool = False) -> dict:
    """tracks: {'dtl': PoseTrack, 'fo': PoseTrack} (eine Ansicht darf fehlen).
    impact_hints_s: Impact-Zeitpunkt je Ansicht in Sekunden ab Clipbeginn (z.B. aus Audio)."""
    impact_hints_s = impact_hints_s or {}
    handed = cfg["golfer"]["handedness"]
    height = float(cfg["golfer"]["body_height_cm"])
    result = {"phases": {}, "metrics": {}, "scale": {}, "warnings": []}
    order = [v for v in ("fo", "dtl") if v in tracks]
    shared = None
    for v in order:
        t = tracks[v]
        hs = impact_hints_s.get(v)
        hint = int(round(hs * t.fps)) if hs is not None else None
        try:
            ph = shared if (synced and shared) else detect_phases(t, hint)
        except ValueError as e:
            result["warnings"].append(f"{v}: {e}")
            continue
        if synced and shared is None:
            shared = ph
        result["phases"][v] = ph
        ppc, how = scale_px_per_cm(t, ph["address"], cfg["cameras"][v].get("px_per_cm", 0), height)
        result["scale"][v] = {"px_per_cm": _r(ppc, 3), "method": how}
        if not np.isfinite(ppc):
            result["warnings"].append(f"{v}: Massstab unbekannt")
            continue
        m = face_on_metrics(t, ph, ppc) if v == "fo" else dtl_metrics(t, ph, ppc, handed)
        result["metrics"].update(m)
    if not result["phases"]:
        return result
    ref = order[0] if order[0] in result["phases"] else next(iter(result["phases"]))
    ph, t = result["phases"][ref], tracks[ref]
    result["metrics"].update(tempo_metrics(ph, t.fps))
    rot_src = "fo" if "fo" in result["phases"] else ref
    result["metrics"].update(rotation_metrics(tracks[rot_src], result["phases"][rot_src], handed))
    return result
