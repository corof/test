"""Overlay-Video (beide Ansichten nebeneinander) und Keyframe-Übersicht."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .analysis import PHASE_LABELS, PHASES, chest, head, pelvis
from .pose import L_ANK, L_HIP, L_SH, R_ANK, R_HIP, R_SH, SKELETON, PoseTrack

LEFT_COLOR, RIGHT_COLOR = (255, 170, 0), (0, 140, 255)  # BGR: links blau, rechts orange
REF_COLOR, TEXT_COLOR = (0, 255, 255), (255, 255, 255)
LEFT_IDS = {11, 13, 15, 23, 25, 27, 29, 31}


def _pt(p):
    return None if not np.all(np.isfinite(p)) else (int(p[0]), int(p[1]))


def draw_pose(img, t: PoseTrack, i: int, thick: int):
    if i >= t.n:
        return
    p = t.px[i]
    for a, b in SKELETON:
        pa, pb = _pt(p[a]), _pt(p[b])
        if pa and pb:
            col = LEFT_COLOR if a in LEFT_IDS and b in LEFT_IDS else (
                RIGHT_COLOR if a not in LEFT_IDS and b not in LEFT_IDS else (200, 200, 200))
            cv2.line(img, pa, pb, col, thick, cv2.LINE_AA)
    for j in range(11, 33):
        q = _pt(p[j])
        if q:
            cv2.circle(img, q, thick + 1, (255, 255, 255), -1, cv2.LINE_AA)


def draw_refs(img, view: str, t: PoseTrack, ph: dict | None, i: int, thick: int):
    if not ph:
        return
    a = ph["address"]
    h = img.shape[0]
    hd, pv = head(t), pelvis(t)
    if view == "fo":
        for arr in (hd, pv):
            q = _pt(arr[a])
            if q:
                cv2.line(img, (q[0], 0), (q[0], h), REF_COLOR, max(1, thick // 2), cv2.LINE_AA)
    else:
        ch = chest(t)
        pa, ca = pv[a], ch[a]
        if np.all(np.isfinite([*pa, *ca])):
            d = ca - pa
            p0, p1 = _pt(pa - 0.3 * d), _pt(ca + 0.6 * d)
            cv2.line(img, p0, p1, REF_COLOR, max(1, thick // 2), cv2.LINE_AA)  # Wirbelsäule
            # Gesäss-Linie: senkrecht hinter dem Becken
            back = np.sign(pa[0] - t.px[a, [L_ANK, R_ANK], 0].mean()) or -1
            hip_w = abs(t.px[a, L_HIP, 0] - t.px[a, R_HIP, 0]) + 0.08 * abs(d[1])
            x = int(pa[0] + back * max(hip_w, 0.12 * abs(d[1])))
            cv2.line(img, (x, int(pa[1] - abs(d[1]) * 0.6)), (x, h), REF_COLOR,
                     max(1, thick // 2), cv2.LINE_AA)
    q = _pt(hd[a])
    if q:
        r = int(0.35 * np.nan_to_num(np.linalg.norm(t.px[a, L_SH] - t.px[a, R_SH]), nan=40) + 15)
        cv2.circle(img, q, r, REF_COLOR, max(1, thick // 2), cv2.LINE_AA)
    cur = [k for k in PHASES if ph[k] == i]
    if cur:
        cv2.putText(img, PHASE_LABELS[cur[0]], (20, 90), cv2.FONT_HERSHEY_SIMPLEX,
                    1.6 * thick / 3, (0, 255, 0), thick, cv2.LINE_AA)


def _fit_h(img, h):
    return cv2.resize(img, (max(1, int(img.shape[1] * h / img.shape[0])), h))


def _letterbox(img, W, H):
    s = min(W / img.shape[1], H / img.shape[0])
    r = cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))))
    out = np.zeros((H, W, 3), np.uint8)
    y, x = (H - r.shape[0]) // 2, (W - r.shape[1]) // 2
    out[y:y + r.shape[0], x:x + r.shape[1]] = r
    return out


def to_h264(src: Path) -> Path:
    """Konvertiert nach H.264 (im Browser abspielbar), falls ffmpeg vorhanden."""
    if not shutil.which("ffmpeg"):
        return src
    dst = src.with_name(src.stem + "_h264.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart",
                        str(dst)], capture_output=True)
    if r.returncode == 0:
        src.unlink(missing_ok=True)
        dst.replace(src)
    return src


def _crop_box(t: PoseTrack, ph: dict, W: int, H: int):
    pts = t.px[ph["address"]:ph["finish"] + 1].reshape(-1, 2)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if not len(pts):
        return 0, 0, W, H
    x0, y0 = np.percentile(pts, 1, axis=0)
    x1, y1 = np.percentile(pts, 99, axis=0)
    mx, my = 0.35 * (x1 - x0), 0.25 * (y1 - y0)
    return (int(max(0, x0 - mx)), int(max(0, y0 - my)),
            int(min(W, x1 + mx)), int(min(H, y1 + my)))


def render(swing_dir: Path, tracks: dict[str, PoseTrack], phases: dict[str, dict],
           out_h: int = 720, slowmo: int = 2) -> dict[str, str | None]:
    """Streamt beide Videos (speicherschonend), schreibt overlay.mp4 und keyframes.jpg."""
    views = [v for v in ("dtl", "fo") if (swing_dir / f"{v}.mp4").exists()]
    caps = {v: cv2.VideoCapture(str(swing_dir / f"{v}.mp4")) for v in views}
    counts = {v: int(caps[v].get(cv2.CAP_PROP_FRAME_COUNT)) for v in views}
    sizes = {v: (int(caps[v].get(cv2.CAP_PROP_FRAME_WIDTH)),
                 int(caps[v].get(cv2.CAP_PROP_FRAME_HEIGHT))) for v in views}
    imp = {v: phases.get(v, {}).get("impact", 0) for v in views}
    lead = max(imp.values()) if imp else 0
    offs = {v: lead - imp[v] for v in views}   # Ansichten über den Impact ausrichten
    total = max((counts[v] + offs[v] for v in views), default=0)
    fps = next((tracks[v].fps for v in views if v in tracks), 60.0)
    thick = max(2, out_h // 240)
    out_video = swing_dir / "overlay.mp4"
    keys: dict[str, dict[str, np.ndarray]] = {v: {} for v in views}
    vw = None
    for k in range(total):
        tiles = []
        for v in views:
            i = k - offs[v]
            ok, img = (caps[v].read() if 0 <= i < counts[v] else (False, None))
            if ok:
                if v in tracks:
                    ph = phases.get(v)
                    if ph:
                        for name in PHASES:
                            if ph[name] == i:
                                kimg = img.copy()
                                draw_refs(kimg, v, tracks[v], ph, -1, thick * 2)
                                draw_pose(kimg, tracks[v], i, thick * 2)
                                keys[v][name] = kimg
                    draw_refs(img, v, tracks[v], ph, i, thick * 2)
                    draw_pose(img, tracks[v], i, thick * 2)
            else:
                img = np.zeros((sizes[v][1], sizes[v][0], 3), np.uint8)
            img = _fit_h(img, out_h)
            cv2.putText(img, v.upper(), (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.0, TEXT_COLOR, 2)
            tiles.append(img)
        canvas = np.hstack(tiles)
        if canvas.shape[1] % 2:
            canvas = canvas[:, :-1]
        if vw is None:
            vw = cv2.VideoWriter(str(out_video), cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps / slowmo, (canvas.shape[1], canvas.shape[0]))
        vw.write(canvas)
    for c in caps.values():
        c.release()
    if vw:
        vw.release()
        to_h264(out_video)

    rows = []
    for v in views:
        if v not in tracks or v not in phases:
            continue
        x0, y0, x1, y1 = _crop_box(tracks[v], phases[v], *sizes[v])
        tiles = []
        for name in PHASES:
            img = keys[v].get(name)
            tile = (_letterbox(img[y0:y1, x0:x1], 300, 420) if img is not None
                    else np.zeros((420, 300, 3), np.uint8))
            cv2.rectangle(tile, (0, 0), (300, 34), (0, 0, 0), -1)
            cv2.putText(tile, f"{v.upper()} {PHASE_LABELS[name]}", (8, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT_COLOR, 2, cv2.LINE_AA)
            tiles.append(tile)
        rows.append(np.hstack(tiles))
    out: dict[str, str | None] = {"overlay": out_video.name if vw else None, "keyframes": None}
    if rows:
        cv2.imwrite(str(swing_dir / "keyframes.jpg"), np.vstack(rows),
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
        out["keyframes"] = "keyframes.jpg"
    return out
