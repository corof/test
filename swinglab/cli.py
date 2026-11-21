"""Kommandozeile: swinglab <befehl> …"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import DEFAULT_PATH, load_config


def _session_or_latest(cfg: dict, s: str | None) -> Path:
    root = Path(cfg["paths"]["data_dir"]) / "sessions"
    if s:
        p = Path(s)
        return p if p.exists() else root / s
    sessions = sorted(p for p in root.glob("*") if p.is_dir())
    if not sessions:
        sys.exit("Keine Sessions gefunden.")
    return sessions[-1]


def cmd_init(cfg, a):
    dst = Path("config.toml")
    if dst.exists() and not a.force:
        sys.exit("config.toml existiert bereits (--force zum Überschreiben).")
    dst.write_text(DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"config.toml angelegt – Kamera-Indizes und Körpergrösse eintragen.")


def cmd_cams(cfg, a):
    from .capture import list_cameras
    found = list_cameras(cfg["capture"]["backend"])
    for i, d in found:
        print(f"  Index {i}: {d}")
    if not found:
        print("  Keine Kamera gefunden.")
    try:
        import sounddevice as sd
        print("\nMikrofone:")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"  {i}: {d['name']}")
    except Exception as e:  # noqa: BLE001
        print(f"(Audio-Geräte nicht abfragbar: {e})")


def cmd_settings(cfg, a):
    from .capture import open_settings_dialog
    open_settings_dialog(cfg["cameras"][a.view], cfg["capture"]["backend"])


def cmd_capture(cfg, a):
    from .capture import new_session_dir, run_cli
    if a.no_analyze:
        cfg["capture"]["auto_analyze"] = False
    session = Path(a.session) if a.session else new_session_dir(cfg)
    print(f"Session: {session}")
    saved = run_cli(cfg, session, preview=a.preview)
    print(f"{len(saved)} Swings gespeichert.")
    if saved:
        from .report import build
        print(f"Report: {build(cfg['paths']['data_dir'])}")


def cmd_import(cfg, a):
    from .pipeline import analyze_swing, import_clips
    session = Path(a.session) if a.session else (
        Path(cfg["paths"]["data_dir"]) / "sessions" / "import")
    out = import_clips(cfg, session, a.dtl, a.fo, a.synced)
    print(f"Importiert nach {out}")
    if not a.no_analyze:
        res = analyze_swing(out, cfg)
        _print_result(out, res)


def _print_result(d: Path, res: dict):
    m = res["metrics"]
    print(f"\n{d.name}: Phasen {json.dumps(next(iter(res['phases'].values()), {}))}")
    for k, v in m.items():
        if not k.endswith("_sign"):
            print(f"  {k:32s} {v}")
    for f in res["findings"]:
        print(f"  ⚠ {f['title']}: {f['detail']}")
    for w in res.get("warnings", []):
        print(f"  ! {w}")


def cmd_analyze(cfg, a):
    from .pipeline import analyze_swing
    target = Path(a.target) if a.target else _session_or_latest(cfg, None)
    dirs = [target] if target.name.startswith("swing_") else sorted(target.glob("swing_*"))
    for d in dirs:
        if (d / "analysis.json").exists() and not a.force:
            continue
        try:
            res = analyze_swing(d, cfg, force=a.force, render_video=not a.no_video)
            _print_result(d, res)
        except Exception as e:  # noqa: BLE001
            print(f"! {d}: {e}")
    from .report import build
    print(f"\nReport: {build(cfg['paths']['data_dir'])}")


def cmd_mevo(cfg, a):
    from .mevo import import_into_session
    session = _session_or_latest(cfg, a.session)
    pairs = import_into_session(session, Path(a.csv), cfg["mevo"]["match_tolerance_s"], a.offset)
    for swing, shot in pairs:
        print(f"  {swing} ← Schuss {shot + 1}")
    print(f"{len(pairs)} Swings mit Mevo+-Daten verknüpft.")
    from .report import build
    print(f"Report: {build(cfg['paths']['data_dir'])}")


def cmd_report(cfg, a):
    from .report import build
    print(build(cfg["paths"]["data_dir"], a.out))


def cmd_calibrate(cfg, a):
    from .calibrate import calibrate
    ppc = calibrate(cfg, a.view, a.length_cm, a.source)
    print(f"{a.view.upper()}: {ppc:.3f} px/cm gespeichert in {cfg['_config_path']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="swinglab", description="Golfschwung-Analyse (DTL + Face-On + Mevo+)")
    p.add_argument("--config", help="Pfad zur config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="config.toml anlegen")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)
    sub.add_parser("cams", help="Kameras und Mikrofone auflisten").set_defaults(fn=cmd_cams)
    s = sub.add_parser("settings", help="Treiber-Einstellungsdialog einer Kamera öffnen (Windows)")
    s.add_argument("view", choices=["dtl", "fo"])
    s.set_defaults(fn=cmd_settings)
    s = sub.add_parser("calibrate", help="Massstab kalibrieren (zwei Punkte, bekannte Länge)")
    s.add_argument("view", choices=["dtl", "fo"])
    s.add_argument("length_cm", type=float, help="z.B. 100 für einen 1-m-Stab")
    s.add_argument("--source", help="Video/Bild statt Live-Kamera")
    s.set_defaults(fn=cmd_calibrate)
    s = sub.add_parser("capture", help="Aufnahme mit Audio-Trigger starten")
    s.add_argument("--session", help="Session-Ordner (Standard: neuer Zeitstempel)")
    s.add_argument("--preview", action="store_true", help="Live-Vorschau beider Kameras")
    s.add_argument("--no-analyze", action="store_true")
    s.set_defaults(fn=cmd_capture)
    s = sub.add_parser("import", help="Externe Videos als Swing importieren")
    s.add_argument("--dtl")
    s.add_argument("--fo")
    s.add_argument("--synced", action="store_true", help="Videos sind bereits zeitgleich geschnitten")
    s.add_argument("--session")
    s.add_argument("--no-analyze", action="store_true")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("analyze", help="Swing oder Session analysieren (Standard: letzte Session)")
    s.add_argument("target", nargs="?")
    s.add_argument("--force", action="store_true", help="auch bereits analysierte neu rechnen")
    s.add_argument("--no-video", action="store_true", help="kein Overlay-Video rendern")
    s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("mevo", help="Mevo+-CSV (FS Golf Export) einer Session zuordnen")
    s.add_argument("csv")
    s.add_argument("--session", help="Standard: letzte Session")
    s.add_argument("--offset", type=int, default=0,
                   help="Reihenfolge-Zuordnung: Swing 1 ↔ Schuss 1+offset (statt Zeitstempel)")
    s.set_defaults(fn=cmd_mevo)
    s = sub.add_parser("report", help="HTML-Report erzeugen")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    a.fn(load_config(a.config), a)


if __name__ == "__main__":
    main()
