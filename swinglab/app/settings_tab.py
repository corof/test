"""Tab «Einstellungen»: Golfer, Kameras, Kalibrierung, Mikrofon, Mevo+-Live."""
from __future__ import annotations

import copy

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

from .common import VideoView

RESOLUTIONS = ["1920x1080", "1280x720", "2560x1440", "640x480"]


class CalibrationDialog(QDialog):
    """Zwei Punkte bekannter Distanz im Live-Bild anklicken → px/cm."""

    def __init__(self, frame: np.ndarray, view: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Kalibrierung {view.upper()}")
        self.resize(1100, 720)
        self.v = VideoView()
        self.v.set_frame(frame)
        self.v.clicked.connect(self._click)
        self.length = QDoubleSpinBox()
        self.length.setRange(10, 500)
        self.length.setValue(100)
        self.length.setSuffix(" cm")
        self.info = QLabel("Stab bekannter Länge in die Ebene des Golfers halten/legen und "
                           "beide Enden anklicken.")
        b_reset = QPushButton("Punkte zurücksetzen")
        b_reset.clicked.connect(self._reset)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(QLabel("Länge"))
        row.addWidget(self.length)
        row.addWidget(b_reset)
        row.addStretch()
        row.addWidget(bb)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.v, 1)
        lay.addLayout(row)

    def _click(self, x, y):
        if len(self.v.marks) >= 2:
            self.v.marks = []
        self.v.marks.append((x, y))
        self.ok.setEnabled(len(self.v.marks) == 2)
        self.v.update()

    def _reset(self):
        self.v.marks = []
        self.ok.setEnabled(False)
        self.v.update()

    def px_per_cm(self) -> float:
        (x0, y0), (x1, y1) = self.v.marks
        return float(np.hypot(x1 - x0, y1 - y0) / self.length.value())


class SettingsTab(QWidget):
    applied = Signal(dict)          # neue Konfiguration
    open_driver_dialog = Signal(str)

    def __init__(self, cfg: dict, engine_getter, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine_getter = engine_getter
        self.w: dict = {}
        body = QWidget()
        grid = QGridLayout(body)

        # Golfer
        g = QGroupBox("Golfer")
        f = QFormLayout(g)
        self.w["hand"] = QComboBox()
        self.w["hand"].addItems(["Rechtshänder", "Linkshänder"])
        self.w["height"] = QSpinBox()
        self.w["height"].setRange(120, 220)
        self.w["height"].setSuffix(" cm")
        f.addRow("Spielhand", self.w["hand"])
        f.addRow("Körpergrösse", self.w["height"])
        grid.addWidget(g, 0, 0)

        # Mikrofon
        g = QGroupBox("Audio-Trigger (Treffgeräusch)")
        f = QFormLayout(g)
        self.w["audio_on"] = QCheckBox("aktiv")
        self.w["audio_dev"] = QComboBox()
        self.w["audio_dev"].setEditable(True)
        try:
            import sounddevice as sd
            self.w["audio_dev"].addItems([""] + [d["name"] for d in sd.query_devices()
                                                 if d["max_input_channels"] > 0])
        except Exception:  # noqa: BLE001
            pass
        self.w["thr"] = QDoubleSpinBox()
        self.w["thr"].setRange(2, 100)
        self.w["min_peak"] = QDoubleSpinBox()
        self.w["min_peak"].setRange(0.005, 1)
        self.w["min_peak"].setDecimals(3)
        self.w["min_peak"].setSingleStep(0.01)
        f.addRow("", self.w["audio_on"])
        f.addRow("Mikrofon", self.w["audio_dev"])
        f.addRow("Faktor über Rauschen", self.w["thr"])
        f.addRow("Mindestpegel", self.w["min_peak"])
        f.addRow(QLabel("<span style='color:#8f8e86'>Löst zu oft aus → Werte erhöhen; "
                        "löst nicht aus → senken. Pegel siehe Aufnahme-Tab.</span>"))
        grid.addWidget(g, 0, 1)

        # Kameras
        for col, v, title in ((0, "dtl", "Kamera Down-the-Line (hinten)"), (1, "fo", "Kamera Face-On (seitlich)")):
            g = QGroupBox(title)
            f = QFormLayout(g)
            idx = QLineEdit()
            idx.setToolTip("Kamera-Nummer (0, 1, …) oder Pfad zu einem Video (Demo)")
            res = QComboBox()
            res.addItems(RESOLUTIONS)
            fps = QSpinBox()
            fps.setRange(15, 240)
            exp_auto = QCheckBox("Automatik")
            exp = QSpinBox()
            exp.setRange(-13, 0)
            exp.setToolTip("DirectShow: -8 ≈ 1/256 s, -9 ≈ 1/512 s, -10 ≈ 1/1024 s")
            exp_auto.toggled.connect(lambda on, e=exp: e.setEnabled(not on))
            ppc = QDoubleSpinBox()
            ppc.setRange(0, 100)
            ppc.setDecimals(3)
            ppc.setSpecialValueText("nicht kalibriert")
            b_cal = QPushButton("Kalibrieren …")
            b_cal.clicked.connect(lambda _=False, v=v: self._calibrate(v))
            b_drv = QPushButton("Treiber-Einstellungen …")
            b_drv.setToolTip("Öffnet den Windows-Kameradialog (Belichtung, Gain, Fokus)")
            b_drv.clicked.connect(lambda _=False, v=v: self.open_driver_dialog.emit(v))
            er = QHBoxLayout()
            er.addWidget(exp)
            er.addWidget(exp_auto)
            cr = QHBoxLayout()
            cr.addWidget(ppc)
            cr.addWidget(b_cal)
            f.addRow("Kamera", idx)
            f.addRow("Auflösung", res)
            f.addRow("Bilder/s", fps)
            f.addRow("Belichtung", er)
            f.addRow("Massstab px/cm", cr)
            f.addRow("", b_drv)
            self.w[v] = dict(idx=idx, res=res, fps=fps, exp=exp, exp_auto=exp_auto, ppc=ppc)
            grid.addWidget(g, 1, col)

        # Aufnahme
        g = QGroupBox("Aufnahme")
        f = QFormLayout(g)
        self.w["pre"] = QDoubleSpinBox()
        self.w["pre"].setRange(0.5, 6)
        self.w["pre"].setSuffix(" s vor Impact")
        self.w["post"] = QDoubleSpinBox()
        self.w["post"].setRange(0.3, 4)
        self.w["post"].setSuffix(" s nach Impact")
        self.w["lat"] = QDoubleSpinBox()
        self.w["lat"].setRange(-0.5, 0.5)
        self.w["lat"].setDecimals(3)
        self.w["lat"].setSuffix(" s")
        self.w["model"] = QComboBox()
        self.w["model"].addItems(["heavy", "full", "lite"])
        f.addRow("Vorlauf", self.w["pre"])
        f.addRow("Nachlauf", self.w["post"])
        f.addRow("Kamera-Latenz", self.w["lat"])
        f.addRow("Pose-Modell", self.w["model"])
        grid.addWidget(g, 2, 0)

        # Mevo+ live
        g = QGroupBox("Mevo+ live (GSPro Open Connect)")
        f = QFormLayout(g)
        self.w["oc"] = QCheckBox("Empfänger aktiv")
        self.w["oc_port"] = QSpinBox()
        self.w["oc_port"].setRange(0, 65535)
        self.w["fw_host"] = QLineEdit()
        self.w["fw_host"].setPlaceholderText("leer = swinglab ist Endpunkt")
        self.w["fw_port"] = QSpinBox()
        self.w["fw_port"].setRange(0, 65535)
        f.addRow("", self.w["oc"])
        f.addRow("Port", self.w["oc_port"])
        f.addRow("Weiterleiten an (GSPro)", self.w["fw_host"])
        f.addRow("Weiterleit-Port", self.w["fw_port"])
        f.addRow(QLabel("<span style='color:#8f8e86'>Ohne Live-Verbindung: Mevo+-CSV im "
                        "Analyse-Tab importieren.</span>"))
        grid.addWidget(g, 2, 1)

        b_apply = QPushButton("Übernehmen && Kameras neu starten")
        b_apply.setObjectName("primary")
        b_apply.clicked.connect(self._apply)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        lay = QVBoxLayout(self)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        self.lbl_path = QLabel()
        self.lbl_path.setObjectName("muted")
        row.addWidget(self.lbl_path, 1)
        row.addWidget(b_apply)
        lay.addLayout(row)
        self.load(cfg)

    def load(self, cfg: dict):
        self.cfg = cfg
        w = self.w
        w["hand"].setCurrentIndex(0 if cfg["golfer"]["handedness"] == "right" else 1)
        w["height"].setValue(int(cfg["golfer"]["body_height_cm"]))
        a = cfg["audio"]
        w["audio_on"].setChecked(bool(a["enabled"]))
        w["audio_dev"].setCurrentText(a["device"])
        w["thr"].setValue(a["threshold_factor"])
        w["min_peak"].setValue(a["min_peak"])
        for v in ("dtl", "fo"):
            c, x = cfg["cameras"][v], w[v]
            x["idx"].setText(str(c["index"]))
            x["res"].setCurrentText(f"{c['width']}x{c['height']}")
            x["fps"].setValue(int(c["fps"]))
            auto = c.get("exposure") in (None, "", "auto")
            x["exp_auto"].setChecked(auto)
            x["exp"].setValue(-9 if auto else int(c["exposure"]))
            x["exp"].setEnabled(not auto)
            x["ppc"].setValue(float(c.get("px_per_cm") or 0))
        cp = cfg["capture"]
        w["pre"].setValue(cp["pre_roll_s"])
        w["post"].setValue(cp["post_roll_s"])
        w["lat"].setValue(cp["video_latency_s"])
        w["model"].setCurrentText(cfg["analysis"]["model"])
        s = cfg["shots"]
        w["oc"].setChecked(bool(s["open_connect"]))
        w["oc_port"].setValue(int(s["port"]))
        w["fw_host"].setText(s["forward_host"])
        w["fw_port"].setValue(int(s["forward_port"]))
        self.lbl_path.setText(f"Einstellungen: {cfg.get('_config_path', '')}  ·  Daten: "
                              f"{cfg['paths']['data_dir']}")

    def collect(self) -> dict:
        c = copy.deepcopy(self.cfg)
        w = self.w
        c["golfer"]["handedness"] = "right" if w["hand"].currentIndex() == 0 else "left"
        c["golfer"]["body_height_cm"] = w["height"].value()
        c["audio"].update(enabled=w["audio_on"].isChecked(), device=w["audio_dev"].currentText(),
                          threshold_factor=w["thr"].value(), min_peak=w["min_peak"].value())
        for v in ("dtl", "fo"):
            x, cam = w[v], c["cameras"][v]
            t = x["idx"].text().strip()
            cam["index"] = int(t) if t.isdigit() else t
            cam["width"], cam["height"] = (int(n) for n in x["res"].currentText().split("x"))
            cam["fps"] = x["fps"].value()
            cam["exposure"] = "auto" if x["exp_auto"].isChecked() else x["exp"].value()
            cam["px_per_cm"] = x["ppc"].value()
        c["capture"].update(pre_roll_s=w["pre"].value(), post_roll_s=w["post"].value(),
                            video_latency_s=w["lat"].value())
        c["analysis"]["model"] = w["model"].currentText()
        c["shots"].update(open_connect=w["oc"].isChecked(), port=w["oc_port"].value(),
                          forward_host=w["fw_host"].text().strip(), forward_port=w["fw_port"].value())
        return c

    def _apply(self):
        self.applied.emit(self.collect())

    def _calibrate(self, view: str):
        eng = self.engine_getter()
        frame = eng.latest(view) if eng else None
        if frame is None:
            QMessageBox.information(self, "Kalibrierung", "Kein Live-Bild dieser Kamera verfügbar.")
            return
        d = CalibrationDialog(frame.copy(), view, self)
        if d.exec() == QDialog.Accepted:
            self.w[view]["ppc"].setValue(d.px_per_cm())
            self._apply()
