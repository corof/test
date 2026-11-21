"""Tab «Analyse»: synchroner Player beider Ansichten mit Skelett, Phasen, Feedback, Mevo+."""
from __future__ import annotations

import html
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ..analysis import PHASE_LABELS, PHASES
from ..overlay import draw_pose, draw_refs
from ..pose import PoseTrack, clean
from ..report import MEVO, METRICS
from ..store import Swing, sessions, swings_of
from .common import VideoView, fmt

MAX_W = 960


class Clip:
    """Ein Video im Speicher (JPEG-komprimiert, verkleinert) + skalierte Pose."""

    def __init__(self, video: Path, pose: Path | None, cfg: dict):
        cap = cv2.VideoCapture(str(video))
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
        self.frames: list[np.ndarray] = []
        self.scale = 1.0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if f.shape[1] > MAX_W:
                self.scale = MAX_W / f.shape[1]
                f = cv2.resize(f, (MAX_W, int(f.shape[0] * self.scale)), interpolation=cv2.INTER_AREA)
            self.frames.append(cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 90])[1])
        cap.release()
        self.track = None
        if pose and pose.exists():
            a = cfg["analysis"]
            t = clean(PoseTrack.load(pose), a["min_visibility"], a["smooth_window_s"], a["max_gap_frames"])
            self.track = PoseTrack(t.px * self.scale, t.vis, t.world, t.fps,
                                   (int(t.size[0] * self.scale), int(t.size[1] * self.scale)))

    def __len__(self):
        return len(self.frames)

    def frame(self, i: int, phases: dict | None, overlay: bool) -> np.ndarray:
        img = cv2.imdecode(self.frames[i], cv2.IMREAD_COLOR)
        if overlay and self.track is not None:
            th = max(2, img.shape[0] // 270)
            draw_refs(img, self._view, self.track, phases, -1, th)
            draw_pose(img, self.track, i, th)
        return img


class ViewerTab(QWidget):
    import_mevo = Signal(Path)
    import_videos = Signal()
    reanalyze = Signal(Path)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.swing: Swing | None = None
        self.clips: dict[str, Clip] = {}
        self.offsets: dict[str, int] = {}
        self.pos, self.total = 0, 0

        # Links: Sessions/Swings
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._on_select)
        b_mevo = QPushButton("Mevo+-CSV importieren …")
        b_mevo.setToolTip("Export aus der FS Golf App der markierten Session zuordnen")
        b_mevo.clicked.connect(self._on_import_mevo)
        b_vid = QPushButton("Videos importieren …")
        b_vid.clicked.connect(self.import_videos)
        self.b_re = QPushButton("Neu analysieren")
        self.b_re.clicked.connect(lambda: self.swing and self.reanalyze.emit(self.swing.dir))
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.tree, 1)
        for b in (b_mevo, b_vid, self.b_re):
            ll.addWidget(b)

        # Mitte: Player
        self.views = {"dtl": VideoView("DTL"), "fo": VideoView("Face-On")}
        vids = QSplitter(Qt.Horizontal)
        for v in self.views.values():
            vids.addWidget(v)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self._seek)
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(48)
        self.btn_play.clicked.connect(self.toggle_play)
        b_prev, b_next = QPushButton("◀ 1"), QPushButton("1 ▶")
        b_prev.clicked.connect(lambda: self.step(-1))
        b_next.clicked.connect(lambda: self.step(1))
        self.speed = QComboBox()
        self.speed.addItems(["1/8×", "1/4×", "1/2×", "1×"])
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self._apply_speed)
        self.chk_overlay = QCheckBox("Skelett + Linien")
        self.chk_overlay.setChecked(True)
        self.chk_overlay.toggled.connect(lambda _: self._render())
        self.lbl_pos = QLabel("–")
        self.lbl_pos.setObjectName("muted")
        ctr = QHBoxLayout()
        for w in (b_prev, self.btn_play, b_next, self.speed, self.chk_overlay):
            ctr.addWidget(w)
        ctr.addWidget(self.slider, 1)
        ctr.addWidget(self.lbl_pos)
        self.phase_bar = QHBoxLayout()
        self.phase_btns = {}
        for p in PHASES:
            b = QPushButton(PHASE_LABELS[p])
            b.clicked.connect(lambda _=False, p=p: self.goto_phase(p))
            self.phase_btns[p] = b
            self.phase_bar.addWidget(b)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(vids, 1)
        cl.addLayout(ctr)
        cl.addLayout(self.phase_bar)

        # Rechts: Details
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(self.info)
        split.setSizes([230, 900, 360])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.step(1, loop=True))
        self.refresh_tree()

    # --- Baum -------------------------------------------------------------
    def refresh_tree(self, select: Path | None = None):
        cur = Path(select) if select else (self.swing.dir if self.swing else None)
        self.tree.blockSignals(True)
        self.tree.clear()
        target = None
        for s in sessions(self.cfg["paths"]["data_dir"]):
            sw = swings_of(s)
            top = QTreeWidgetItem([f"{s.name}  ({len(sw)})"])
            top.setData(0, Qt.UserRole, str(s))
            self.tree.addTopLevelItem(top)
            for w in sw:
                it = QTreeWidgetItem([f"#{w.name[-3:]}  {w.summary()}"])
                it.setData(0, Qt.UserRole, str(w.dir))
                it.setToolTip(0, w.summary())
                top.addChild(it)
                if cur and w.dir.resolve() == cur.resolve():
                    target = it
        if target:
            target.parent().setExpanded(True)
            self.tree.setCurrentItem(target)
        elif self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)
        self.tree.blockSignals(False)

    def selected_session(self) -> Path | None:
        it = self.tree.currentItem()
        if not it:
            return None
        p = Path(it.data(0, Qt.UserRole))
        return p if not p.name.startswith("swing_") else p.parent

    def _on_import_mevo(self):
        s = self.selected_session()
        if s:
            self.import_mevo.emit(s)

    def _on_select(self):
        it = self.tree.currentItem()
        if it and Path(it.data(0, Qt.UserRole)).name.startswith("swing_"):
            self.load(Path(it.data(0, Qt.UserRole)))

    # --- Laden ------------------------------------------------------------
    def load(self, swing_dir: Path):
        self.timer.stop()
        self.btn_play.setText("▶")
        self.swing = Swing.load(swing_dir)
        self.clips = {}
        for v in ("dtl", "fo"):
            vid = swing_dir / f"{v}.mp4"
            if vid.exists():
                c = Clip(vid, swing_dir / f"pose_{v}.npz", self.cfg)
                c._view = v
                self.clips[v] = c
        ph = self.swing.analysis.get("phases", {})
        imp = {v: ph.get(v, {}).get("impact", 0) for v in self.clips}
        lead = max(imp.values(), default=0)
        self.offsets = {v: lead - imp[v] for v in self.clips}
        self.total = max((len(c) + self.offsets[v] for v, c in self.clips.items()), default=0)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, self.total - 1))
        self.slider.blockSignals(False)
        for v, view in self.views.items():
            view.setVisible(v in self.clips)
        self._apply_speed()
        self._fill_info()
        start = self._global(self._ref_view(), ph.get(self._ref_view(), {}).get("address", 0)) if ph else 0
        self._seek(start)

    def _ref_view(self) -> str:
        return "fo" if "fo" in self.clips else next(iter(self.clips), "fo")

    def _global(self, view: str, i: int) -> int:
        return i + self.offsets.get(view, 0)

    # --- Wiedergabe -----------------------------------------------------
    def _apply_speed(self):
        fps = next(iter(self.clips.values())).fps if self.clips else 60
        factor = [1 / 8, 1 / 4, 1 / 2, 1][self.speed.currentIndex()]
        self.timer.setInterval(max(5, int(1000 / (fps * factor))))

    def toggle_play(self):
        if self.timer.isActive():
            self.timer.stop()
            self.btn_play.setText("▶")
        elif self.total:
            self.timer.start()
            self.btn_play.setText("⏸")

    def step(self, d: int, loop: bool = False):
        if not self.total:
            return
        n = self.pos + d
        if loop and n >= self.total:
            n = 0
        self._seek(max(0, min(self.total - 1, n)))

    def goto_phase(self, p: str):
        ph = (self.swing.analysis.get("phases") or {}).get(self._ref_view()) if self.swing else None
        if ph:
            self.timer.stop()
            self.btn_play.setText("▶")
            self._seek(self._global(self._ref_view(), ph[p]))

    def _seek(self, k: int):
        self.pos = int(k)
        self.slider.blockSignals(True)
        self.slider.setValue(self.pos)
        self.slider.blockSignals(False)
        self._render()

    def _render(self):
        if not self.swing:
            return
        phases = self.swing.analysis.get("phases", {})
        label = ""
        for v, c in self.clips.items():
            i = self.pos - self.offsets[v]
            if 0 <= i < len(c):
                self.views[v].set_frame(c.frame(i, phases.get(v), self.chk_overlay.isChecked()))
                cur = [PHASE_LABELS[p] for p in PHASES if phases.get(v, {}).get(p) == i]
                self.views[v].overlay_text = f"{v.upper()}  {cur[0] if cur else ''}"
                if v == self._ref_view():
                    ph = phases.get(v, {})
                    imp = ph.get("impact")
                    if imp is not None:
                        label = f"Frame {i}  ·  {1000 * (i - imp) / c.fps:+.0f} ms zum Impact"
                    else:
                        label = f"Frame {i}"
            else:
                self.views[v].set_frame(None)
        self.lbl_pos.setText(label)

    # --- Details ----------------------------------------------------------
    def _fill_info(self):
        s = self.swing
        e = html.escape
        h = [f"<h3 style='margin:0'>{e(s.label)}</h3>"
             f"<p style='color:#8f8e86;margin-top:2px'>{e(s.meta.get('recorded_at', ''))}"
             f"{' · ' + e(str(s.mevo.get('club'))) if s.mevo.get('club') else ''}</p>"]
        if not s.analyzed:
            h.append("<p>Noch nicht analysiert.</p>")
        for f in s.findings:
            h.append(f"<p style='margin:10px 0 2px'><b style='color:#f79009'>{e(f['title'])}</b></p>"
                     f"<p style='margin:0'>{e(f['detail'])}</p>"
                     f"<p style='margin:2px 0 0;color:#c3c2b7'><i>Drill:</i> {e(f['drill'])}</p>")
        if s.failed:
            h.append("<p style='color:#f79009'>Golfer nicht erkannt – Licht, Belichtung und "
                     "Bildausschnitt prüfen.</p>")
        elif s.analyzed and not s.findings:
            h.append("<p style='color:#3fb950'>Keine Auffälligkeiten über den Schwellwerten.</p>")
        h.append("<h4 style='margin-bottom:4px'>Körper</h4><table width='100%' cellspacing=2>")
        for k, label, unit, band in METRICS:
            v = s.metrics.get(k)
            if v is None:
                continue
            bad = band and not (band[0] <= v <= band[1])
            col = "#f79009" if bad else "#f0efec"
            h.append(f"<tr><td style='color:#c3c2b7'>{e(label)}</td>"
                     f"<td align=right style='color:{col}'>{fmt(v, 1, unit)}</td></tr>")
        h.append("</table>")
        if s.mevo:
            units = s.mevo.get("units", {})
            h.append("<h4 style='margin-bottom:4px'>Mevo+</h4><table width='100%' cellspacing=2>")
            for k, label in MEVO:
                if s.mevo.get(k) is not None:
                    h.append(f"<tr><td style='color:#c3c2b7'>{e(label)}</td>"
                             f"<td align=right>{fmt(s.mevo[k], 1, units.get(k, ''))}</td></tr>")
            h.append("</table>")
        else:
            h.append("<p style='color:#8f8e86'>Keine Mevo+-Daten zugeordnet.</p>")
        sc = s.analysis.get("scale", {})
        if sc:
            h.append("<p style='color:#8f8e86;font-size:11px'>Massstab: " + ", ".join(
                f"{v.upper()} {x.get('method')}" for v, x in sc.items()) + ". * = Schätzung aus 3D-Pose.</p>")
        for w in s.analysis.get("warnings", []):
            h.append(f"<p style='color:#f79009;font-size:11px'>{e(w)}</p>")
        self.info.setHtml("".join(h))
