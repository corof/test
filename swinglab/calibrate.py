"""Massstab-Kalibrierung: zwei Punkte bekannter Distanz im Bild anklicken."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def grab_frame(cfg: dict, view: str, source: str | None) -> np.ndarray:
    if source:
        cap = cv2.VideoCapture(source)
        ok, f = cap.read()
        cap.release()
    else:
        from .capture import open_camera
        cap = open_camera(cfg["cameras"][view], cfg["capture"]["backend"])
        for _ in range(15):  # Belichtung einschwingen lassen
            ok, f = cap.read()
        cap.release()
    if not ok:
        raise RuntimeError("Kein Bild erhalten")
    return f


def click_two_points(img: np.ndarray, title: str) -> tuple[np.ndarray, np.ndarray]:
    pts: list[tuple[int, int]] = []
    scale = min(1.0, 1400 / img.shape[1])
    disp = cv2.resize(img, None, fx=scale, fy=scale)

    def on_mouse(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 2:
            pts.append((x, y))

    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_mouse)
    while True:
        d = disp.copy()
        cv2.putText(d, "Zwei Punkte bekannter Distanz anklicken (r = neu, Enter = OK)",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        for p in pts:
            cv2.circle(d, p, 6, (0, 0, 255), -1)
        if len(pts) == 2:
            cv2.line(d, pts[0], pts[1], (0, 0, 255), 2)
        cv2.imshow(title, d)
        k = cv2.waitKey(20) & 0xFF
        if k == ord("r"):
            pts.clear()
        if k in (13, 10) and len(pts) == 2:
            break
        if k == 27:
            cv2.destroyAllWindows()
            raise KeyboardInterrupt
    cv2.destroyAllWindows()
    return np.array(pts[0]) / scale, np.array(pts[1]) / scale


def calibrate(cfg: dict, view: str, length_cm: float, source: str | None = None) -> float:
    img = grab_frame(cfg, view, source)
    a, b = click_two_points(img, f"Kalibrierung {view.upper()}")
    ppc = float(np.linalg.norm(a - b) / length_cm)
    from .config import set_value_in_toml
    path = Path(cfg["_config_path"])
    if not path.exists():
        from .config import DEFAULT_PATH
        path.write_text(DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    set_value_in_toml(path, f"cameras.{view}", "px_per_cm", round(ppc, 4))
    return ppc
