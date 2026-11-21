"""Synthetischer Rechtshänder-Swing mit bekannten Werten (für Tests)."""
from __future__ import annotations

import numpy as np

from swinglab.pose import (L_ANK, L_EAR, L_EL, L_HIP, L_KNEE, L_SH, L_WR, NOSE, R_ANK, R_EAR,
                           R_EL, R_HIP, R_KNEE, R_SH, R_WR, PoseTrack)

FPS = 60.0
PPC = 4.0           # px pro cm
HEIGHT = 180.0      # cm -> Beinlänge 0.49*180 = 88.2 cm = 352.8 px
T_ADDR, T_TOP, T_IMP, T_FIN, T_END = 1.0, 1.8, 2.07, 2.6, 3.5


def _profile(t, knots, values):
    """stückweise glatt (Cosinus) interpolierte Kurve"""
    t = np.asarray(t)
    out = np.full_like(t, values[0], dtype=float)
    for (t0, t1), (v0, v1) in zip(zip(knots[:-1], knots[1:]), zip(values[:-1], values[1:])):
        m = (t >= t0) & (t <= t1)
        u = (t[m] - t0) / (t1 - t0)
        out[m] = v0 + (v1 - v0) * (1 - np.cos(np.pi * u)) / 2
    out[t > knots[-1]] = values[-1]
    return out


def make_swing(hip_sway_top_cm=-10.0, hip_slide_imp_cm=6.0, head_imp_cm=2.0,
               early_ext_cm=7.0, view_flip_fo=False):
    n = int(T_END * FPS)
    t = np.arange(n) / FPS
    K = [0, T_ADDR, T_TOP, T_IMP, T_FIN]
    theta = np.radians(_profile(t, K, [0, 0, -160, 0, 150]))  # Handwinkel (0 = unten)
    hip_dx = _profile(t, K, [0, 0, hip_sway_top_cm, hip_slide_imp_cm, hip_slide_imp_cm + 4]) * PPC
    head_dx = _profile(t, K, [0, 0, -3, head_imp_cm, 8]) * PPC
    ee = _profile(t, K, [0, 0, 1, early_ext_cm, early_ext_cm]) * PPC
    sh_yaw = np.radians(_profile(t, K, [0, 0, 90, -20, -100]))
    hip_yaw = np.radians(_profile(t, K, [0, 0, 45, -40, -80]))

    def frame_fo(i):
        p = np.full((33, 2), np.nan)
        s = -1 if view_flip_fo else 1     # Ziel rechts im Bild (Standard)
        cx = 960
        hy, ky, ay = 600, 600 + 176.4, 600 + 352.8
        hx = cx + s * hip_dx[i]
        p[L_HIP], p[R_HIP] = (hx + s * 60, hy), (hx - s * 60, hy)
        p[L_KNEE], p[R_KNEE] = (cx + s * 70, ky), (cx - s * 70, ky)
        p[L_ANK], p[R_ANK] = (cx + s * 70, ay), (cx - s * 70, ay)
        shx, shy = cx + s * hip_dx[i] * 0.7, 350
        p[L_SH], p[R_SH] = (shx + s * 80, shy), (shx - s * 80, shy)
        hdx = cx + s * head_dx[i]
        p[NOSE], p[L_EAR], p[R_EAR] = (hdx, 250), (hdx + 25 * s, 245), (hdx - 25 * s, 245)
        R = 330
        hand = np.array([shx + s * R * np.sin(theta[i]), shy + R * np.cos(theta[i])])
        p[L_WR], p[R_WR] = hand + (4 * s, 0), hand - (4 * s, 0)
        p[L_EL], p[R_EL] = (p[L_SH] + hand) / 2, (p[R_SH] + hand) / 2
        return p

    def frame_dtl(i):
        p = np.full((33, 2), np.nan)
        # Ball rechts im Bild; Becken bewegt sich bei Early Extension nach rechts
        px_ = 800 + ee[i]
        hy = 600
        p[L_HIP], p[R_HIP] = (px_ - 5, hy), (px_ + 5, hy)
        knee = (px_ + 90, hy + np.sqrt(176.4 ** 2 - 90 ** 2))
        ank = (px_ + 30, knee[1] + np.sqrt(176.4 ** 2 - 60 ** 2))
        p[L_KNEE] = p[R_KNEE] = knee
        p[L_ANK] = p[R_ANK] = ank
        lean = np.radians(35 - 0.6 * ee[i] / PPC)   # Vorneigung, verliert durch EE
        ch = np.array([px_ + 260 * np.sin(lean), hy - 260 * np.cos(lean)])
        p[L_SH], p[R_SH] = ch + (5, 0), ch - (5, 0)
        p[NOSE] = p[L_EAR] = p[R_EAR] = ch + (60, -60)
        hand = np.array([ch[0] + 150 + 120 * np.sin(theta[i] / 2), ch[1] + 300 * np.cos(theta[i])])
        p[L_WR] = p[R_WR] = hand
        p[L_EL] = p[R_EL] = (ch + hand) / 2
        return p

    def world(i):
        w = np.full((33, 3), np.nan)
        for (l, r), yaw, half in (((L_SH, R_SH), sh_yaw[i], 0.2), ((L_HIP, R_HIP), hip_yaw[i], 0.15)):
            v = half * np.array([np.cos(yaw), 0, np.sin(yaw)])
            w[l], w[r] = v, -v
        w[L_EL], w[L_WR] = w[L_SH] + (0, 0.3, 0), w[L_SH] + (0, 0.6, 0)  # gestreckter Arm
        return w

    vis = np.ones((n, 33))
    W = np.array([world(i) for i in range(n)])
    fo = PoseTrack(np.array([frame_fo(i) for i in range(n)]), vis, W, FPS, (1920, 1080))
    dtl = PoseTrack(np.array([frame_dtl(i) for i in range(n)]), vis, W, FPS, (1920, 1080))
    return {"fo": fo, "dtl": dtl}
