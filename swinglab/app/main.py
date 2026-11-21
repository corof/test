"""SwingLab – Desktop-App: Aufnahme, Schwungdaten und Analyse in einer Software."""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget)

from ..config import DEFAULT_PATH, load_config, save_config


def app_home() -> Path:
    """Arbeitsordner: Dokumente/SwingLab (Einstellungen, Daten, Modelle)."""
    env = os.environ.get("SWINGLAB_HOME")
    home = Path(env) if env else Path.home() / "Documents" / "SwingLab"
    home.mkdir(parents=True, exist_ok=True)
    return home


class Bridge(QObject):
    """Leitet Ereignisse aus Hintergrund-Threads sicher in den UI-Thread."""
    swing_saved = Signal(str)
    swing_analyzed = Signal(str)
    status = Signal(str)
    shot = Signal(dict)


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        from .live_tab import LiveTab
        from .settings_tab import SettingsTab
        from .trends_tab import TrendsTab
        from .viewer_tab import ViewerTab

        self.cfg = cfg
        self.engine = None
        self.server = None
        self.session_dir: Path | None = None
        self.pool = ProcessPoolExecutor(max_workers=1)
        self.pending: set[str] = set()
        self.bridge = Bridge()
        self.bridge.swing_saved.connect(self._on_swing_saved)
        self.bridge.swing_analyzed.connect(self._on_swing_analyzed)
        self.bridge.status.connect(self._status)
        self.bridge.shot.connect(self._on_shot)
        from ..shots import ShotMatcher
        self.matcher = ShotMatcher(after_s=cfg["shots"]["match_window_s"])

        self.setWindowTitle("SwingLab")
        self.resize(1500, 950)
        self.tabs = QTabWidget()
        self.live = LiveTab()
        self.viewer = ViewerTab(cfg)
        self.trends = TrendsTab(cfg, self.open_report)
        self.settings = SettingsTab(cfg, lambda: self.engine)
        self.tabs.addTab(self.live, "Aufnahme")
        self.tabs.addTab(self.viewer, "Analyse")
        self.tabs.addTab(self.trends, "Verlauf")
        self.tabs.addTab(self.settings, "Einstellungen")
        self.setCentralWidget(self.tabs)
        self.tabs.currentChanged.connect(self._on_tab)

        self.live.session_toggled.connect(self._toggle_session)
        self.live.manual_save.connect(lambda: self.engine and self.engine.trigger_manual())
        self.live.open_swing.connect(self.open_swing)
        self.viewer.import_mevo.connect(self.import_mevo)
        self.viewer.import_videos.connect(self.import_videos)
        self.viewer.reanalyze.connect(lambda p: self.analyze(p, force=True))
        self.settings.applied.connect(self.apply_settings)
        self.settings.open_driver_dialog.connect(self.driver_dialog)

        self.start_engine()
        self.start_shot_server()
        self.live.set_session(None)

    # --- Engine / Kameras ------------------------------------------------
    def start_engine(self):
        from ..capture import CaptureEngine
        self.engine = CaptureEngine(self.cfg, self.session_dir,
                                    on_swing=lambda p: self.bridge.swing_saved.emit(str(p)),
                                    on_status=self.bridge.status.emit)
        st = self.engine.start(wait_s=8)
        self.engine.armed = self.session_dir is not None
        self.live.set_engine(self.engine)
        msg = " · ".join(f"{k.upper()}: {v}" for k, v in st.items())
        self._status(msg)

    def stop_engine(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
            self.live.set_engine(None)

    def start_shot_server(self):
        s = self.cfg["shots"]
        if self.server:
            self.server.stop()
            self.server = None
        if not s["open_connect"]:
            return
        from ..shots import OpenConnectServer
        fw = (s["forward_host"], int(s["forward_port"])) if s["forward_host"] else None
        self.server = OpenConnectServer(self.bridge.shot.emit, int(s["port"]), forward=fw,
                                        on_status=self.bridge.status.emit)
        self.server.start()

    def apply_settings(self, cfg: dict):
        cfg["_config_path"] = self.cfg.get("_config_path")
        self.cfg.clear()
        self.cfg.update(cfg)
        save_config(self.cfg)
        self.stop_engine()
        self.start_engine()
        self.start_shot_server()
        self.settings.load(self.cfg)
        self._status("Einstellungen gespeichert, Kameras neu gestartet. " + self.statusBar().currentMessage())

    def driver_dialog(self, view: str):
        from ..capture import open_settings_dialog
        self.stop_engine()
        try:
            open_settings_dialog(self.cfg["cameras"][view], self.cfg["capture"]["backend"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Kamera", str(e))
        self.start_engine()

    # --- Session ---------------------------------------------------------
    def _toggle_session(self, on: bool):
        if on:
            self.session_dir = (Path(self.cfg["paths"]["data_dir"]) / "sessions"
                                / datetime.now().strftime("%Y-%m-%d_%H%M"))
            self.session_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.session_dir = None
        if self.engine:
            self.engine.session_dir = self.session_dir
            self.engine.armed = on
        self.live.set_session(self.session_dir)
        self.viewer.refresh_tree()

    def _on_swing_saved(self, path: str):
        p = Path(path)
        self._status(f"{p.parent.name}/{p.name} gespeichert – Analyse läuft …")
        from ..store import Swing
        meta = Swing.load(p).meta
        if meta.get("impact_wallclock"):
            from ..shots import write_shot
            for d, shot in self.matcher.add_swing(p, datetime.fromisoformat(meta["impact_wallclock"])):
                write_shot(d, shot)
        self.live.show_swing(p)
        self.analyze(p)

    def _on_shot(self, shot: dict):
        from ..shots import write_shot
        txt = f"Mevo+: Ball {shot.get('ball_speed', 0):.1f} mph"
        if shot.get("carry") is not None:
            txt += f", Carry {shot['carry']:.0f}"
        self._status(txt)
        for d, s in self.matcher.add_shot(shot):
            write_shot(d, s)
            self.live.show_swing(d)
            self.viewer.refresh_tree()

    # --- Analyse ---------------------------------------------------------
    def analyze(self, swing_dir: Path, force: bool = False):
        from ..pipeline import analyze_swing_job
        key = str(swing_dir)
        if key in self.pending:
            return
        self.pending.add(key)
        fut = self.pool.submit(analyze_swing_job, key, dict(self.cfg), force)
        fut.add_done_callback(lambda f, k=key: self.bridge.swing_analyzed.emit(
            k + "\n" + (f.result() if not f.exception() else str(f.exception()))))

    def _on_swing_analyzed(self, payload: str):
        path, _, msg = payload.partition("\n")
        self.pending.discard(path)
        p = Path(path)
        self._status(f"{p.parent.name}/{p.name}: {msg}")
        if self.session_dir and p.parent == self.session_dir:
            self.live.show_swing(p)
        self.viewer.refresh_tree()
        if self.viewer.swing and self.viewer.swing.dir == p:
            self.viewer.load(p)

    def open_swing(self, p: Path):
        self.tabs.setCurrentWidget(self.viewer)
        self.viewer.refresh_tree(select=p)
        self.viewer.load(p)

    # --- Import ------------------------------------------------------------
    def import_mevo(self, session: Path):
        fn, _ = QFileDialog.getOpenFileName(self, "Mevo+-Export (CSV) wählen", str(Path.home()),
                                            "CSV (*.csv *.txt);;Alle Dateien (*)")
        if not fn:
            return
        from ..mevo import import_into_session
        try:
            pairs = import_into_session(session, Path(fn), self.cfg["mevo"]["match_tolerance_s"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Mevo+-Import", f"Datei konnte nicht gelesen werden:\n{e}")
            return
        QMessageBox.information(self, "Mevo+-Import",
                                f"{len(pairs)} Swings in {session.name} mit Schussdaten verknüpft.")
        self.viewer.refresh_tree()
        if self.viewer.swing:
            self.viewer.load(self.viewer.swing.dir)

    def import_videos(self):
        dtl, _ = QFileDialog.getOpenFileName(self, "Video Down-the-Line (abbrechen = keins)",
                                             str(Path.home()), "Videos (*.mp4 *.mov *.avi *.mkv)")
        fo, _ = QFileDialog.getOpenFileName(self, "Video Face-On (abbrechen = keins)",
                                            str(Path.home()), "Videos (*.mp4 *.mov *.avi *.mkv)")
        if not dtl and not fo:
            return
        from ..pipeline import import_clips
        session = Path(self.cfg["paths"]["data_dir"]) / "sessions" / "import"
        out = import_clips(self.cfg, session, dtl or None, fo or None)
        self.viewer.refresh_tree(select=out)
        self.analyze(out)

    # --- Diverses --------------------------------------------------------
    def open_report(self):
        from ..report import build
        out = build(self.cfg["paths"]["data_dir"])
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out.resolve())))

    def _on_tab(self, i: int):
        if self.tabs.widget(i) is self.trends:
            self.trends.refresh()
        elif self.tabs.widget(i) is self.viewer:
            self.viewer.refresh_tree()

    def _status(self, msg: str):
        self.statusBar().showMessage(msg)
        if self.session_dir and ("gespeichert" in msg or "erkannt" in msg):
            self.live.set_status(msg)

    def closeEvent(self, ev):
        self.stop_engine()
        if self.server:
            self.server.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(ev)


def selftest(out: Path) -> int:
    """Prüft das (gepackte) Programm ohne Fenster: Abhängigkeiten, Modell, Pose-Inferenz."""
    import tempfile
    import traceback

    lines, ok = [], True

    def check(name, fn):
        nonlocal ok
        try:
            lines.append(f"OK   {name}: {fn()}")
        except Exception:  # noqa: BLE001
            ok = False
            lines.append(f"FAIL {name}:\n{traceback.format_exc()}")

    import numpy as np
    check("numpy", lambda: np.__version__)
    check("opencv", lambda: __import__("cv2").__version__)
    check("sounddevice", lambda: __import__("sounddevice").get_portaudio_version()[1])
    check("config", lambda: len(load_config(Path(tempfile.gettempdir()) / "none.toml")))

    def pose():
        import cv2
        from ..pose import ensure_model, estimate_pose
        m = ensure_model(Path(tempfile.gettempdir()) / "swinglab_models", "heavy")
        vid = Path(tempfile.gettempdir()) / "swinglab_selftest.mp4"
        vw = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
        for _ in range(5):
            vw.write(np.zeros((240, 320, 3), np.uint8))
        vw.release()
        t = estimate_pose(vid, m)
        return f"Modell {m.name}, {t.n} Frames verarbeitet"
    check("mediapipe", pose)

    def gui():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        from .common import Sparkline
        w = Sparkline("t", "cm", [1.0, 2.0], ["a", "b"])
        return f"Qt ok ({w.grab().width()} px)"
    check("qt", gui)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if ok else 1


def main(argv=None):
    multiprocessing.freeze_support()
    ap = argparse.ArgumentParser(prog="SwingLab")
    ap.add_argument("--home", help="Arbeitsordner (Standard: Dokumente/SwingLab)")
    ap.add_argument("--selftest", metavar="DATEI", nargs="?", const="selftest.txt",
                    help="Komponenten prüfen (Kamera-Backend, Audio, MediaPipe) und beenden")
    a, _ = ap.parse_known_args(argv)
    if a.selftest:
        sys.exit(selftest(Path(a.selftest)))
    if a.home:
        os.environ["SWINGLAB_HOME"] = a.home
    home = app_home()
    os.chdir(home)
    cfg_path = home / "config.toml"
    if not cfg_path.exists():
        cfg_path.write_text(DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(cfg_path)
    cfg["paths"]["data_dir"] = str((home / cfg["paths"]["data_dir"]).resolve())
    cfg["paths"]["model_dir"] = str((home / cfg["paths"]["model_dir"]).resolve())

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("SwingLab")
    from .common import apply_dark_theme
    apply_dark_theme(app)
    w = MainWindow(cfg)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
