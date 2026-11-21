"""Pose-Schätzung (MediaPipe Pose Landmarker, 33 Körperpunkte) pro Video."""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import savgol_filter

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_{v}/float16/latest/pose_landmarker_{v}.task")

# MediaPipe-Indizes
NOSE, L_EAR, R_EAR = 0, 7, 8
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 11, 12, 13, 14, 15, 16
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK = 23, 24, 25, 26, 27, 28
L_HEEL, R_HEEL, L_FOOT, R_FOOT = 29, 30, 31, 32

SKELETON = [(L_SH, R_SH), (L_SH, L_EL), (L_EL, L_WR), (R_SH, R_EL), (R_EL, R_WR),
            (L_SH, L_HIP), (R_SH, R_HIP), (L_HIP, R_HIP), (L_HIP, L_KNEE), (L_KNEE, L_ANK),
            (R_HIP, R_KNEE), (R_KNEE, R_ANK), (L_ANK, L_HEEL), (L_HEEL, L_FOOT),
            (R_ANK, R_HEEL), (R_HEEL, R_FOOT), (L_FOOT, L_ANK), (R_FOOT, R_ANK)]


@dataclass
class PoseTrack:
    px: np.ndarray        # (T, 33, 2) Bildkoordinaten in Pixel (y nach unten), NaN = fehlt
    vis: np.ndarray       # (T, 33) Sichtbarkeit 0..1
    world: np.ndarray     # (T, 33, 3) metrische 3D-Koordinaten (m), Hüftmitte = Ursprung
    fps: float
    size: tuple[int, int]  # (w, h)

    @property
    def n(self) -> int:
        return len(self.px)

    def save(self, path: Path):
        np.savez_compressed(path, px=self.px, vis=self.vis, world=self.world,
                            fps=self.fps, size=np.array(self.size))

    @classmethod
    def load(cls, path: Path) -> "PoseTrack":
        d = np.load(path)
        return cls(d["px"], d["vis"], d["world"], float(d["fps"]), tuple(int(x) for x in d["size"]))


def ensure_model(model_dir: str | Path, variant: str = "heavy") -> Path:
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"pose_landmarker_{variant}.task"
    if not path.exists():
        print(f"Lade MediaPipe-Modell '{variant}' …")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL.format(v=variant), tmp)
        tmp.replace(path)
    return path


def video_info(path: str | Path) -> tuple[float, int, tuple[int, int]]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return fps, n, size


def estimate_pose(video: str | Path, model_path: Path, min_conf: float = 0.5) -> PoseTrack:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO, num_poses=1,
        min_pose_detection_confidence=min_conf, min_pose_presence_confidence=min_conf,
        min_tracking_confidence=min_conf)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    px, vis, world = [], [], []
    w = h = 0
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            res = lm.detect_for_video(img, int(round(i * 1000 / fps)))
            if res.pose_landmarks:
                p = res.pose_landmarks[0]
                q = res.pose_world_landmarks[0]
                px.append([[l.x * w, l.y * h] for l in p])
                vis.append([l.visibility if l.visibility is not None else 1.0 for l in p])
                world.append([[l.x, l.y, l.z] for l in q])
            else:
                px.append(np.full((33, 2), np.nan))
                vis.append(np.zeros(33))
                world.append(np.full((33, 3), np.nan))
            i += 1
    cap.release()
    if i == 0:
        raise RuntimeError(f"Video leer oder nicht lesbar: {video}")
    return PoseTrack(np.array(px, float), np.array(vis, float), np.array(world, float),
                     float(fps), (w, h))


def _fill_gaps(a: np.ndarray, max_gap: int) -> np.ndarray:
    """Lineare Interpolation über NaN-Lücken ≤ max_gap entlang Achse 0 (jede Spalte)."""
    a = a.copy()
    flat = a.reshape(len(a), -1)
    t = np.arange(len(a))
    for c in range(flat.shape[1]):
        col = flat[:, c]
        bad = np.isnan(col)
        if not bad.any() or bad.all():
            continue
        filled = np.interp(t, t[~bad], col[~bad])
        # nur kurze Lücken füllen (Ränder: bis max_gap)
        run_start = None
        for i in range(len(col) + 1):
            b = i < len(col) and bad[i]
            if b and run_start is None:
                run_start = i
            elif not b and run_start is not None:
                if i - run_start <= max_gap:
                    col[run_start:i] = filled[run_start:i]
                run_start = None
        flat[:, c] = col
    return flat.reshape(a.shape)


def clean(track: PoseTrack, min_vis: float = 0.5, smooth_s: float = 0.1,
          max_gap: int = 12) -> PoseTrack:
    """Unsichere Punkte verwerfen, Lücken füllen, glätten (Savitzky-Golay)."""
    px = track.px.copy()
    world = track.world.copy()
    low = track.vis < min_vis
    # Handgelenke im Schwung oft verdeckt/unscharf -> toleranter
    low[:, [L_WR, R_WR]] = track.vis[:, [L_WR, R_WR]] < min_vis * 0.5
    px[low] = np.nan
    world[low] = np.nan
    px = _fill_gaps(px, max_gap)
    world = _fill_gaps(world, max_gap)
    win = max(5, int(round(smooth_s * track.fps)) | 1)
    if track.n > win:
        for arr in (px, world):
            flat = arr.reshape(track.n, -1)
            for c in range(flat.shape[1]):
                col = flat[:, c]
                ok = ~np.isnan(col)
                if ok.sum() > win:
                    # nur zusammenhängend gültige Bereiche glätten
                    seg = np.flatnonzero(np.diff(np.r_[0, ok.astype(int), 0]))
                    for s, e in zip(seg[::2], seg[1::2]):
                        if e - s > win:
                            col[s:e] = savgol_filter(col[s:e], win, 2)
                flat[:, c] = col
    return PoseTrack(px, track.vis, world, track.fps, track.size)
