"""Tab «Verlauf»: Kennzahlen über alle Swings, Baustellen, Korrelationen mit Mevo+."""
from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..report import METRICS, correlations
from ..store import all_swings
from .common import Card, Sparkline


class TrendsTab(QWidget):
    def __init__(self, cfg: dict, open_report, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.filter = QComboBox()
        self.filter.addItems(["Alle Swings", "Letzte 50", "Letzte 20"])
        self.filter.currentIndexChanged.connect(self.refresh)
        self.club = QComboBox()
        self.club.currentIndexChanged.connect(self.refresh)
        b_rep = QPushButton("HTML-Report öffnen")
        b_rep.clicked.connect(open_report)
        self.lbl = QLabel()
        self.lbl.setObjectName("muted")
        top = QHBoxLayout()
        top.addWidget(QLabel("Zeitraum"))
        top.addWidget(self.filter)
        top.addWidget(QLabel("Schläger"))
        top.addWidget(self.club)
        top.addWidget(self.lbl, 1)
        top.addWidget(b_rep)

        self.inner = QWidget()
        self.grid = QVBoxLayout(self.inner)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.inner)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(scroll, 1)

    def _swings(self):
        sw = all_swings(self.cfg["paths"]["data_dir"])
        clubs = sorted({str(s.mevo.get("club")) for s in sw if s.mevo.get("club")})
        cur = self.club.currentText()
        self.club.blockSignals(True)
        self.club.clear()
        self.club.addItems(["Alle"] + clubs)
        self.club.setCurrentText(cur if cur in clubs else "Alle")
        self.club.blockSignals(False)
        if self.club.currentText() != "Alle":
            sw = [s for s in sw if str(s.mevo.get("club")) == self.club.currentText()]
        n = [None, 50, 20][self.filter.currentIndex()]
        return sw[-n:] if n else sw

    def refresh(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        sw = self._swings()
        self.lbl.setText(f"{len(sw)} analysierte Swings")
        if not sw:
            self.grid.addWidget(QLabel("Noch keine analysierten Swings."))
            return

        # Baustellen
        cnt = Counter(f["key"] for s in sw for f in s.findings)
        titles = {f["key"]: (f["title"], f["drill"]) for s in sw for f in s.findings}
        if cnt:
            c = Card("Grösste Baustellen")
            for key, k in cnt.most_common(4):
                t, d = titles[key]
                lab = QLabel(f"<b>{t}</b> – in {k} von {len(sw)} Swings ({100 * k / len(sw):.0f} %)"
                             f"<br><span style='color:#c3c2b7'>Drill: {d}</span>")
                lab.setWordWrap(True)
                c.lay.addWidget(lab)
            self.grid.addWidget(c)

        # Sparklines
        labels = [s.label for s in sw]
        box = QWidget()
        g = QGridLayout(box)
        g.setSpacing(10)
        k = 0
        for key, label, unit, band in METRICS:
            vals = [s.metrics.get(key) for s in sw]
            if sum(v is not None for v in vals) < 2:
                continue
            g.addWidget(Sparkline(label, unit, vals, labels, band), k // 3, k % 3)
            k += 1
        hint = QLabel("Grünes Band = Zielbereich · * = Schätzung aus 3D-Pose (für Trends) · "
                      "Maus über die Kurve zeigt einzelne Swings")
        hint.setObjectName("muted")
        self.grid.addWidget(hint)
        self.grid.addWidget(box)

        # Korrelationen
        rows = correlations([{"analysis": s.analysis, "mevo": s.mevo} for s in sw])
        c = Card("Was beeinflusst deinen Ballflug?")
        if rows:
            note = QLabel("Korrelation Körper ↔ Mevo+. |r| ≥ 0.5 ist deutlich, ab ~20 Swings belastbar. "
                          "Steigung: Änderung des Mevo-Werts pro Einheit der Körper-Kennzahl.")
            note.setWordWrap(True)
            note.setObjectName("muted")
            c.lay.addWidget(note)
            t = QTableWidget(len(rows), 5)
            t.setHorizontalHeaderLabels(["Körper", "Mevo+", "r", "Steigung", "n"])
            for i, (_, ml, vl, r, slope, unit, n) in enumerate(rows):
                for j, val in enumerate([ml, vl, f"{r:+.2f}", f"{slope:+.2f} / {unit}", str(n)]):
                    it = QTableWidgetItem(val)
                    if j >= 2:
                        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    t.setItem(i, j, it)
            t.resizeColumnsToContents()
            t.horizontalHeader().setStretchLastSection(True)
            t.setMinimumHeight(min(420, 34 + 30 * len(rows)))
            c.lay.addWidget(t)
        else:
            c.lay.addWidget(QLabel("Ab 8 Swings mit Mevo+-Daten erscheinen hier die Zusammenhänge."))
        self.grid.addWidget(c)
        self.grid.addStretch()
