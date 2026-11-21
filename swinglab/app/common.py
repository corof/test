"""Gemeinsame Widgets und Helfer für die App."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

ACCENT = QColor("#3fb950")
SERIES = QColor("#3987e5")
BAND = QColor(25, 158, 112, 46)
WARN = QColor("#f79009")
MUTED = QColor("#8f8e86")


def cv_to_qimage(bgr: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def apply_dark_theme(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    base, alt, text = QColor("#1a1a19"), QColor("#242422"), QColor("#f0efec")
    for role, col in [(QPalette.Window, base), (QPalette.Base, QColor("#141413")),
                      (QPalette.AlternateBase, alt), (QPalette.Button, alt),
                      (QPalette.WindowText, text), (QPalette.Text, text),
                      (QPalette.ButtonText, text), (QPalette.ToolTipBase, alt),
                      (QPalette.ToolTipText, text), (QPalette.Highlight, QColor("#2f6fbf")),
                      (QPalette.HighlightedText, QColor("#ffffff")),
                      (QPalette.PlaceholderText, MUTED)]:
        p.setColor(role, col)
    p.setColor(QPalette.Disabled, QPalette.Text, MUTED)
    p.setColor(QPalette.Disabled, QPalette.ButtonText, MUTED)
    app.setPalette(p)
    app.setStyleSheet("""
        QPushButton { padding: 6px 12px; border-radius: 6px; border: 1px solid #3a3a37;
                      background: #2a2a28; }
        QPushButton:hover { background: #33332f; }
        QPushButton:checked { background: #1f6f3a; border-color: #3fb950; }
        QPushButton#primary { background: #1f6f3a; border-color: #3fb950; font-weight: 600; }
        QPushButton#primary:checked { background: #8a2b2b; border-color: #e5534b; }
        QTabWidget::pane { border: none; }
        QTabBar::tab { padding: 8px 18px; font-size: 14px; }
        QTabBar::tab:selected { border-bottom: 2px solid #3fb950; }
        QFrame#card { background: #242422; border: 1px solid #363633; border-radius: 10px; }
        QLabel#h1 { font-size: 18px; font-weight: 600; }
        QLabel#muted { color: #8f8e86; }
        QGroupBox { border: 1px solid #363633; border-radius: 8px; margin-top: 14px; padding: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
    """)


class Card(QFrame):
    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 10, 12, 12)
        if title:
            t = QLabel(title)
            t.setObjectName("h1")
            self.lay.addWidget(t)


class VideoView(QWidget):
    """Zeigt ein Bild seitenverhältnistreu; meldet Klicks in Bildkoordinaten."""
    clicked = Signal(float, float)

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self._img: QImage | None = None
        self.placeholder = placeholder
        self.overlay_text = ""
        self.marks: list[tuple[float, float]] = []
        self.setMinimumSize(240, 160)

    def set_frame(self, bgr: np.ndarray | None):
        self._img = None if bgr is None else cv_to_qimage(bgr)
        self.update()

    def _target(self) -> QRectF:
        if not self._img:
            return QRectF(self.rect())
        iw, ih = self._img.width(), self._img.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0d0c"))
        if self._img:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            r = self._target()
            p.drawImage(r, self._img)
            if self.marks:
                s = r.width() / self._img.width()
                pts = [QPointF(r.x() + x * s, r.y() + y * s) for x, y in self.marks]
                p.setPen(QPen(QColor("#ff4040"), 2))
                for q in pts:
                    p.drawEllipse(q, 6, 6)
                if len(pts) == 2:
                    p.drawLine(pts[0], pts[1])
        else:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignCenter, self.placeholder)
        if self.overlay_text:
            p.setPen(QColor("#ffffff"))
            f = QFont()
            f.setBold(True)
            p.setFont(f)
            p.fillRect(QRectF(8, 8, p.fontMetrics().horizontalAdvance(self.overlay_text) + 16, 26),
                       QColor(0, 0, 0, 150))
            p.drawText(QRectF(16, 8, 600, 26), Qt.AlignVCenter, self.overlay_text)

    def mousePressEvent(self, ev):
        if not self._img:
            return
        r = self._target()
        if r.contains(ev.position()):
            s = self._img.width() / r.width()
            self.clicked.emit((ev.position().x() - r.x()) * s, (ev.position().y() - r.y()) * s)


class LevelMeter(QWidget):
    """Audiopegel relativ zur Auslöseschwelle (Markierung bei 1.0)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value, self.peak = 0.0, 0.0
        self.setFixedHeight(14)
        self.setMinimumWidth(120)

    def set_value(self, v: float):
        self.value = max(0.0, v)
        self.peak = max(self.value, self.peak * 0.93)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect().adjusted(0, 2, -1, -2)
        p.fillRect(r, QColor("#141413"))
        full = 1.5
        w = r.width() * min(self.value, full) / full
        p.fillRect(QRectF(r.x(), r.y(), w, r.height()), ACCENT if self.value < 1 else QColor("#e5534b"))
        x = r.x() + r.width() * min(self.peak, full) / full
        p.fillRect(QRectF(x - 1, r.y(), 2, r.height()), QColor("#f0efec"))
        tx = r.x() + r.width() / full
        p.setPen(QPen(WARN, 2))
        p.drawLine(QPointF(tx, r.y() - 2), QPointF(tx, r.bottom() + 2))


class Sparkline(QWidget):
    """Einzelne Kennzahl über alle Swings, mit Zielbereich und Hover-Wert."""

    def __init__(self, title: str, unit: str, values: list, labels: list[str], band=None, parent=None):
        super().__init__(parent)
        self.title, self.unit, self.values, self.labels, self.band = title, unit, values, labels, band
        self.hover: int | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(240, 130)

    def _geom(self):
        pts = [(i, v) for i, v in enumerate(self.values) if v is not None]
        vs = [v for _, v in pts] + (list(self.band) if self.band else [])
        lo, hi = (min(vs), max(vs)) if vs else (0, 1)
        pad = (hi - lo) * 0.1 or 1
        lo, hi = lo - pad, hi + pad
        top, bot, left, right = 46, self.height() - 10, 10, self.width() - 10
        n = max(1, len(self.values) - 1)
        X = lambda i: left + (right - left) * i / n  # noqa: E731
        Y = lambda v: top + (bot - top) * (1 - (v - lo) / (hi - lo))  # noqa: E731
        return pts, X, Y

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#242422"))
        p.setPen(QColor("#f0efec"))
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.drawText(10, 18, self.title)
        f.setBold(False)
        p.setFont(f)
        pts, X, Y = self._geom()
        valid = [v for _, v in pts]
        p.setPen(MUTED)
        if valid:
            i = self.hover if self.hover is not None and self.values[self.hover] is not None else pts[-1][0]
            txt = (f"{self.labels[i]}: {self.values[i]:.1f} {self.unit}   ·   Ø {np.mean(valid):.1f}"
                   f"  σ {np.std(valid):.1f}")
            p.drawText(10, 36, txt)
        if self.band:
            y0, y1 = Y(self.band[1]), Y(self.band[0])
            p.fillRect(QRectF(0, y0, self.width(), y1 - y0), BAND)
        if len(pts) >= 2:
            path = QPainterPath(QPointF(X(pts[0][0]), Y(pts[0][1])))
            for i, v in pts[1:]:
                path.lineTo(X(i), Y(v))
            p.setPen(QPen(SERIES, 2))
            p.drawPath(path)
        for i, v in pts:
            p.setPen(QPen(QColor("#242422"), 2))
            p.setBrush(SERIES)
            r = 5 if i == self.hover else 4
            p.drawEllipse(QPointF(X(i), Y(v)), r, r)

    def mouseMoveEvent(self, ev):
        pts, X, _ = self._geom()
        if not pts:
            return
        x = ev.position().x()
        self.hover = min(pts, key=lambda iv: abs(X(iv[0]) - x))[0]
        self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.update()


def fmt(v, nd=1, unit=""):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if isinstance(v, (int, float)):
        return f"{round(v, nd) + 0.0:.{nd}f}{(' ' + unit) if unit else ''}"
    return str(v)
