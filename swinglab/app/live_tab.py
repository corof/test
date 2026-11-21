"""Tab «Aufnahme»: Live-Bild beider Kameras, Session, letzter Swing."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ..store import Swing, swings_of
from .common import Card, LevelMeter, VideoView, fmt


class LiveTab(QWidget):
    open_swing = Signal(Path)
    session_toggled = Signal(bool)
    manual_save = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = None
        self.session_dir: Path | None = None

        # Kopfzeile
        self.btn_session = QPushButton("●  Session starten")
        self.btn_session.setObjectName("primary")
        self.btn_session.setCheckable(True)
        self.btn_session.toggled.connect(self._toggle)
        self.btn_manual = QPushButton("Swing speichern (manuell)")
        self.btn_manual.setToolTip("Kurz nach dem Schlag drücken, falls der Audio-Trigger nicht auslöst")
        self.btn_manual.clicked.connect(self.manual_save)
        self.btn_manual.setEnabled(False)
        self.lbl_session = QLabel("Keine Session aktiv")
        self.lbl_session.setObjectName("muted")
        self.meter = LevelMeter()
        self.meter.setToolTip("Mikrofonpegel – über der orangen Marke löst der Trigger aus")
        head = QHBoxLayout()
        head.addWidget(self.btn_session)
        head.addWidget(self.btn_manual)
        head.addSpacing(12)
        head.addWidget(self.lbl_session, 1)
        head.addWidget(QLabel("Mikrofon"))
        head.addWidget(self.meter)

        # Kameras
        self.views = {"dtl": VideoView("DTL – keine Kamera"), "fo": VideoView("Face-On – keine Kamera")}
        cams = QSplitter(Qt.Horizontal)
        for v in ("dtl", "fo"):
            cams.addWidget(self.views[v])

        # Letzter Swing + Liste
        self.last = Card("Letzter Swing")
        self.last_img = QLabel()
        self.last_img.setAlignment(Qt.AlignCenter)
        self.last_img.setMinimumHeight(120)
        self.last_text = QLabel("Noch kein Swing in dieser Session.")
        self.last_text.setWordWrap(True)
        self.last_text.setTextFormat(Qt.RichText)
        self.last.lay.addWidget(self.last_img)
        self.last.lay.addWidget(self.last_text)
        self.last.lay.addStretch()
        self.list = QListWidget()
        self.list.setIconSize(QSize(96, 54))
        self.list.itemActivated.connect(lambda it: self.open_swing.emit(Path(it.data(Qt.UserRole))))
        lst = Card("Swings dieser Session")
        lst.lay.addWidget(self.list)
        hint = QLabel("Doppelklick öffnet den Swing in der Analyse.")
        hint.setObjectName("muted")
        lst.lay.addWidget(hint)

        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(self.last)
        bottom.addWidget(lst)
        bottom.setSizes([700, 400])
        main = QSplitter(Qt.Vertical)
        main.addWidget(cams)
        main.addWidget(bottom)
        main.setSizes([520, 300])

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(main, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def set_engine(self, engine):
        self.engine = engine

    def _toggle(self, on: bool):
        self.btn_session.setText("■  Session beenden" if on else "●  Session starten")
        self.btn_manual.setEnabled(on)
        self.session_toggled.emit(on)

    def set_session(self, session_dir: Path | None):
        self.session_dir = session_dir
        if session_dir:
            self.lbl_session.setText(f"Session {session_dir.name} – bereit, einfach schlagen")
        else:
            self.lbl_session.setText("Keine Session aktiv – Kameras laufen nur als Vorschau")
        self.refresh_list()

    def set_status(self, text: str):
        self.lbl_session.setText(text)

    def _tick(self):
        if not self.engine:
            return
        for v, view in self.views.items():
            f = self.engine.latest(v)
            if f is not None:
                view.set_frame(f)
                view.overlay_text = f"{v.upper()}  {self.engine.fps(v):.0f} fps"
        self.meter.set_value(self.engine.audio_level)

    def refresh_list(self):
        self.list.clear()
        if not self.session_dir or not self.session_dir.exists():
            return
        for s in reversed(swings_of(self.session_dir)):
            it = QListWidgetItem(f"#{s.name[-3:]}   {s.summary()}")
            it.setData(Qt.UserRole, str(s.dir))
            kf = s.dir / "keyframes.jpg"
            if kf.exists():
                pm = QPixmap(str(kf))
                # Ausschnitt: Impact der oberen Zeile
                w = pm.width() // 6
                it.setIcon(QIcon(pm.copy(4 * w, 0, w, pm.height() // 2)))
            self.list.addItem(it)

    def show_swing(self, swing_dir: Path):
        s = Swing.load(swing_dir)
        kf = swing_dir / "keyframes.jpg"
        if kf.exists():
            pm = QPixmap(str(kf))
            self.last_img.setPixmap(pm.scaled(self.last.width() - 30, 260, Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))
        else:
            self.last_img.setPixmap(QPixmap())
            self.last_img.setText("Golfer nicht erkannt" if s.failed else "Analyse läuft …")
        m = s.metrics
        rows = [("Tempo", fmt(m.get("tempo_ratio"), 1, ": 1")),
                ("Kopf → Ziel (Impact)", fmt(m.get("head_sway_impact_cm"), 1, "cm")),
                ("Becken am Top", fmt(m.get("hip_sway_top_cm"), 1, "cm")),
                ("Early Extension", fmt(m.get("early_extension_cm"), 1, "cm"))]
        for k, l in (("club_speed", "Schlägerkopf"), ("carry", "Carry"), ("club_path", "Path"),
                     ("face_angle", "Face")):
            if s.mevo.get(k) is not None:
                rows.append((f"Mevo+ {l}", fmt(s.mevo[k], 1, s.mevo.get("units", {}).get(k, ""))))
        html = "<table cellspacing=4>" + "".join(
            f"<tr><td style='color:#8f8e86'>{a}</td><td><b>{b}</b></td></tr>" for a, b in rows) + "</table>"
        if s.findings:
            f = s.findings[0]
            html += (f"<p style='margin-top:8px'><b style='color:#f79009'>{f['title']}</b><br>"
                     f"{f['detail']}<br><i>Drill:</i> {f['drill']}</p>")
        elif s.failed:
            html += ("<p style='color:#f79009'>Analyse fehlgeschlagen: "
                     + "; ".join(s.analysis.get("warnings", [])) + "<br>Licht, Belichtung und "
                     "Bildausschnitt prüfen (ganzer Körper sichtbar?).</p>")
        elif s.analyzed:
            html += "<p style='color:#3fb950'>Keine Auffälligkeiten über den Schwellwerten.</p>"
        self.last_text.setText(f"<b>{s.label}</b><br>{html}")
        self.refresh_list()
