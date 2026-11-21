"""Erkennung des Treffgeräusches (Impact) aus Audio – live und offline."""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy import signal


def _highpass(sr: int, hz: float):
    return signal.butter(4, hz, btype="highpass", fs=sr, output="sos")


def detect_impacts(samples: np.ndarray, sr: int, highpass_hz: float = 2500,
                   threshold_factor: float = 12.0, min_peak: float = 0.08,
                   refractory_s: float = 1.0) -> list[float]:
    """Liefert Zeitpunkte (s) scharfer Transienten. Es zählt jeweils der
    *erste* Peak; der Ballaufprall auf der Leinwand ~0.1 s später fällt in die Totzeit."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < sr // 10:
        return []
    y = np.abs(signal.sosfiltfilt(_highpass(sr, highpass_hz), x))
    win = max(1, int(0.002 * sr))
    env = np.maximum.reduceat(y, np.arange(0, len(y), win))
    # Rauschboden: gleitender Median über 0.5 s
    k = max(3, int(0.5 * sr / win) | 1)
    floor = signal.medfilt(env, k) + 1e-6
    hits, last = [], -1e9
    for i in np.flatnonzero((env > threshold_factor * floor) & (env > min_peak)):
        t = i * win / sr
        if t - last >= refractory_s:
            seg = y[i * win:(i + 1) * win]
            hits.append(t + (int(np.argmax(seg)) / sr if len(seg) else 0.0))
            last = t
    return hits


def audio_from_video(path: str, sr: int = 48000) -> np.ndarray | None:
    """Extrahiert Mono-Audio via ffmpeg; None, falls keine Tonspur."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
             "-f", "f32le", "-"], capture_output=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    a = np.frombuffer(out, dtype=np.float32)
    return a if a.size else None


@dataclass
class LiveDetectorConfig:
    samplerate: int = 48000
    highpass_hz: float = 2500
    threshold_factor: float = 12.0
    min_peak: float = 0.08
    refractory_s: float = 3.0
    device: str = ""


class LiveImpactDetector:
    """Hört auf das Mikrofon und ruft `on_impact(t_perf)` mit dem Zeitpunkt des
    Treffers in `time.perf_counter()`-Zeit auf."""

    def __init__(self, cfg: LiveDetectorConfig, on_impact: Callable[[float], None]):
        self.cfg, self.on_impact = cfg, on_impact
        self.sos = _highpass(cfg.samplerate, cfg.highpass_hz)
        self.zi = signal.sosfilt_zi(self.sos) * 0.0
        self.noise = 1e-3
        self.last = -1e9
        self.level = 0.0  # für Pegelanzeige
        self._stream = None
        self._lock = threading.Lock()

    def _device(self):
        if not self.cfg.device:
            return None
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if self.cfg.device.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                return i
        raise RuntimeError(f"Mikrofon '{self.cfg.device}' nicht gefunden")

    def process_block(self, block: np.ndarray, t_block_end: float) -> float | None:
        """Verarbeitet einen Mono-Block; gibt ggf. Impact-Zeit zurück (testbar ohne Hardware)."""
        y, self.zi = signal.sosfilt(self.sos, block.astype(np.float64), zi=self.zi)
        y = np.abs(y)
        peak = float(y.max()) if y.size else 0.0
        self.level = peak
        hit = None
        now_idx = int(np.argmax(y)) if y.size else 0
        t_peak = t_block_end - (len(y) - now_idx) / self.cfg.samplerate
        if (peak > self.cfg.threshold_factor * self.noise and peak > self.cfg.min_peak
                and t_peak - self.last >= self.cfg.refractory_s):
            self.last = t_peak
            hit = t_peak
        else:
            # Rauschboden langsam nachführen (nur ohne Treffer)
            rms = float(np.sqrt(np.mean(y ** 2))) if y.size else 0.0
            self.noise = 0.995 * self.noise + 0.005 * max(rms, 1e-5)
        return hit

    def start(self):
        import sounddevice as sd

        def cb(indata, frames, time_info, status):
            t_end = time.perf_counter() - (self._stream.latency if self._stream else 0.0)
            hit = self.process_block(indata[:, 0].copy(), t_end)
            if hit is not None:
                self.on_impact(hit)

        self._stream = sd.InputStream(samplerate=self.cfg.samplerate, channels=1,
                                      blocksize=512, device=self._device(), callback=cb)
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
