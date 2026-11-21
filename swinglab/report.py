"""Statischer HTML-Report (offline, keine externen Abhängigkeiten).

Enthält: häufigste Baustellen, Verläufe der Kennzahlen über alle Swings,
Zusammenhänge Körper ↔ Mevo+ (Korrelation) und Detailkarten je Swing.
"""
from __future__ import annotations

import html
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

METRICS = [  # key, Label, Einheit, Zielbereich (lo, hi) oder None
    ("tempo_ratio", "Tempo Rück:Ab", ":1", (2.5, 3.6)),
    ("head_sway_impact_cm", "Kopf → Ziel bei Impact", "cm", (-6, 5)),
    ("head_vertical_impact_cm", "Kopf vertikal bei Impact", "cm", (-5, 5)),
    ("hip_sway_top_cm", "Becken am Top (− = vom Ziel weg)", "cm", (-8, 4)),
    ("hip_slide_impact_cm", "Becken → Ziel bei Impact", "cm", (2, 15)),
    ("early_extension_cm", "Early Extension (Becken → Ball)", "cm", (-5, 5)),
    ("spine_angle_address_deg", "Vorneigung Address", "°", None),
    ("spine_angle_change_impact_deg", "Δ Vorneigung bis Impact", "°", (-6, 6)),
    ("trail_knee_change_top_deg", "Δ hinteres Knie bis Top", "°", (-15, 15)),
    ("shoulder_turn_top_deg", "Schulterrotation Top*", "°", None),
    ("hip_turn_top_deg", "Hüftrotation Top*", "°", None),
    ("x_factor_top_deg", "X-Factor Top*", "°", None),
    ("hip_open_impact_deg", "Hüfte offen bei Impact*", "°", None),
    ("pelvis_lead_ms", "Becken führt Schultern*", "ms", (0, 80)),
    ("lead_elbow_top_deg", "Führungsarm Ellbogen Top*", "°", (145, 180)),
    ("backswing_s", "Rückschwung", "s", None),
    ("downswing_s", "Abschwung", "s", None),
]
MEVO = [("club_speed", "Schlägerkopf"), ("ball_speed", "Ballgeschw."), ("smash", "Smash"),
        ("attack_angle", "Anstellwinkel"), ("club_path", "Club Path"), ("face_angle", "Face"),
        ("face_to_path", "Face-to-Path"), ("launch_angle", "Abflugwinkel"),
        ("spin_rate", "Spin"), ("spin_axis", "Spinachse"), ("carry", "Carry"),
        ("offline", "Abweichung")]

CSS = """
:root{--surface:#fcfcfb;--surface-2:#f1f0ed;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#8a8984;
--line:#e2e1dc;--series:#2a78d6;--band:rgba(27,175,122,.12);--warn:#b54708;--good:#067647;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--surface:#1a1a19;
--surface-2:#242422;--ink:#f0efec;--ink-2:#c3c2b7;--ink-3:#8f8e86;--line:#363633;--series:#3987e5;
--band:rgba(25,158,112,.18);--warn:#f79009;--good:#47cd89;}}
:root[data-theme="dark"]{--surface:#1a1a19;--surface-2:#242422;--ink:#f0efec;--ink-2:#c3c2b7;
--ink-3:#8f8e86;--line:#363633;--series:#3987e5;--band:rgba(25,158,112,.18);--warn:#f79009;--good:#47cd89;}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1240px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 12px}
.sub{color:var(--ink-2)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(270px,1fr))}
.card{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h3{font-size:14px;margin:0 0 2px;font-weight:600}.card .v{font-size:12px;color:var(--ink-2)}
svg text{fill:var(--ink-3);font-size:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--ink-2);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
.flag{color:var(--warn);font-weight:600}.ok{color:var(--good)}
.swing{margin-top:20px}.swing img,.swing video{width:100%;border-radius:8px;display:block}
.cols{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:900px){.cols{grid-template-columns:3fr 2fr}}
.finding{border-left:3px solid var(--warn);padding:4px 10px;margin:8px 0}
.finding b{display:block}.finding small{color:var(--ink-2);display:block}
.scroll{overflow-x:auto}details summary{cursor:pointer;color:var(--ink-2)}
.note{font-size:12px;color:var(--ink-3)}
"""


def collect(data_dir: Path) -> list[dict]:
    swings = []
    for a in sorted(data_dir.glob("sessions/*/swing_*/analysis.json")):
        d = a.parent
        rec = {"dir": d, "session": d.parent.name, "name": d.name,
               "analysis": json.loads(a.read_text(encoding="utf-8"))}
        for f in ("meta", "mevo"):
            p = d / f"{f}.json"
            rec[f] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        swings.append(rec)
    return swings


def _fmt(v, nd=1):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if isinstance(v, (int, float)):
        return f"{round(v, nd) + 0.0:.{nd}f}"  # kein "-0.0"
    return html.escape(str(v))


def sparkline(values: list, labels: list[str], band, unit: str, w=260, h=90) -> str:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return '<p class="note">zu wenige Werte</p>'
    vs = [v for _, v in pts] + ([band[0], band[1]] if band else [])
    lo, hi = min(vs), max(vs)
    pad = (hi - lo) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    n = len(values)
    X = lambda i: 8 + (w - 16) * (i / max(1, n - 1))  # noqa: E731
    Y = lambda v: 6 + (h - 20) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    if band:
        y0, y1 = Y(band[1]), Y(band[0])
        out.append(f'<rect x="0" y="{y0:.1f}" width="{w}" height="{y1 - y0:.1f}" fill="var(--band)"/>')
    path = " ".join(f"{'M' if k == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for k, (i, v) in enumerate(pts))
    out.append(f'<path d="{path}" fill="none" stroke="var(--series)" stroke-width="2" '
               'stroke-linejoin="round" stroke-linecap="round"/>')
    for i, v in pts:
        out.append(f'<g><circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="9" fill="transparent"/>'
                   f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" fill="var(--series)" '
                   f'stroke="var(--surface-2)" stroke-width="2"/>'
                   f'<title>{html.escape(labels[i])}: {v:.1f} {unit}</title></g>')
    out.append(f'<text x="2" y="{h - 2}">{html.escape(labels[0])}</text>'
               f'<text x="{w - 2}" y="{h - 2}" text-anchor="end">{html.escape(labels[-1])}</text>')
    out.append("</svg>")
    return "".join(out)


def correlations(swings: list[dict], min_n: int = 8) -> list[tuple]:
    rows = []
    for mk, ml, unit, _ in METRICS:
        for vk, vl in MEVO:
            xs, ys = [], []
            for s in swings:
                x, y = s["analysis"]["metrics"].get(mk), s["mevo"].get(vk)
                if x is not None and y is not None:
                    xs.append(float(x))
                    ys.append(float(y))
            if len(xs) >= min_n and np.ptp(xs) > 0.05 and np.ptp(ys) > 0.05:
                r = float(np.corrcoef(xs, ys)[0, 1])
                slope = r * float(np.std(ys) / np.std(xs))
                rows.append((abs(r), ml, vl, r, slope, unit, len(xs)))
    rows.sort(reverse=True)
    return rows[:15]


def build(data_dir: str | Path, out: str | Path | None = None) -> Path:
    data_dir = Path(data_dir)
    out = Path(out) if out else data_dir / "report.html"
    swings = collect(data_dir)
    base = out.parent
    labels = [f"{s['session'][5:]} #{s['name'][-3:]}" for s in swings]

    # Häufigste Baustellen
    agg = defaultdict(lambda: [0, 0.0, ""])
    for s in swings:
        for f in s["analysis"].get("findings", []):
            a = agg[f["key"]]
            a[0] += 1
            a[1] += f["severity"]
            a[2] = f["title"]
            a.append(f["drill"]) if len(a) == 3 else None
    top = sorted(agg.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)

    h = [f"<!doctype html><html lang='de'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Swing-Analyse</title><style>{CSS}</style></head><body><main>",
         f"<h1>Swing-Analyse</h1><p class='sub'>{len(swings)} Swings aus "
         f"{len({s['session'] for s in swings})} Sessions</p>"]
    if not swings:
        h.append("<p>Noch keine analysierten Swings. <code>swinglab capture</code> oder "
                 "<code>swinglab analyze</code> ausführen.</p>")
    if top:
        n = len(swings)
        h.append("<h2>Grösste Baustellen</h2><div class='grid'>")
        for key, (cnt, sev, title, *drill) in top[:4]:
            h.append(f"<div class='card'><h3>{html.escape(title)}</h3>"
                     f"<div class='v'>in {cnt} von {n} Swings ({100 * cnt / n:.0f} %)</div>"
                     f"<p style='margin:6px 0 0;font-size:13px'>{html.escape(drill[0] if drill else '')}</p></div>")
        h.append("</div>")

    if swings:
        h.append("<h2>Verlauf</h2><p class='note'>Grünes Band = Zielbereich. "
                 "* = Schätzung aus 3D-Pose (nur für Trends, nicht absolut).</p><div class='grid'>")
        for key, label, unit, band in METRICS:
            vals = [s["analysis"]["metrics"].get(key) for s in swings]
            if sum(v is not None for v in vals) < 2:
                continue
            last = next((v for v in reversed(vals) if v is not None), None)
            valid = [v for v in vals if v is not None]
            h.append(f"<div class='card'><h3>{html.escape(label)}</h3>"
                     f"<div class='v'>letzter {_fmt(last)} {unit} · Ø {np.mean(valid):.1f} · "
                     f"σ {np.std(valid):.1f}</div>{sparkline(vals, labels, band, unit)}</div>")
        h.append("</div>")

        corr = correlations(swings)
        if corr:
            h.append("<h2>Was beeinflusst deinen Ballflug?</h2><p class='note'>Korrelation "
                     "Körper ↔ Mevo+ über alle Swings. |r| ≥ 0.5 ist deutlich, ab ca. 20 Swings "
                     "belastbar. Korrelation ≠ Kausalität – aber ein guter Hinweis, wo man ansetzt.</p>"
                     "<div class='scroll'><table><tr><th>Körper</th><th>Mevo+</th><th class='num'>r</th>"
                     "<th class='num'>Steigung</th><th class='num'>n</th></tr>")
            for _, ml, vl, r, slope, unit, n in corr:
                h.append(f"<tr><td>{html.escape(ml)}</td><td>{html.escape(vl)}</td>"
                         f"<td class='num'>{r:+.2f}</td><td class='num'>{slope:+.2f} pro {unit}</td>"
                         f"<td class='num'>{n}</td></tr>")
            h.append("</table></div>")
        elif sum(bool(s["mevo"]) for s in swings) > 0:
            h.append("<p class='note'>Für Korrelationen mit Mevo+ werden mindestens 8 Swings "
                     "mit Schussdaten benötigt.</p>")

        h.append("<h2>Alle Swings</h2><div class='scroll'><table><tr><th>Swing</th>"
                 + "".join(f"<th class='num'>{html.escape(l)}</th>" for _, l, _, _ in METRICS[:6])
                 + "<th class='num'>Carry</th><th class='num'>Path</th><th class='num'>Face</th>"
                 "<th>Hauptpunkt</th></tr>")
        for s, lab in zip(reversed(swings), reversed(labels)):
            m = s["analysis"]["metrics"]
            f = s["analysis"].get("findings", [])
            h.append(f"<tr><td><a href='#{s['session']}-{s['name']}'>{lab}</a></td>"
                     + "".join(f"<td class='num'>{_fmt(m.get(k))}</td>" for k, *_ in METRICS[:6])
                     + "".join(f"<td class='num'>{_fmt(s['mevo'].get(k))}</td>"
                               for k in ("carry", "club_path", "face_angle"))
                     + f"<td>{html.escape(f[0]['title']) if f else '<span class=ok>✓</span>'}</td></tr>")
        h.append("</table></div>")

        h.append("<h2>Details</h2>")
        for s, lab in zip(reversed(swings), reversed(labels)):
            a, d = s["analysis"], s["dir"]
            rel = lambda p: os.path.relpath(d / p, base).replace(os.sep, "/")  # noqa: E731
            media = a.get("media") or {}
            h.append(f"<section class='swing card' id='{s['session']}-{s['name']}'>"
                     f"<h3>{lab} <span class='v'>{html.escape(s['meta'].get('recorded_at', ''))}"
                     f"{' · ' + html.escape(str(s['mevo'].get('club'))) if s['mevo'].get('club') else ''}"
                     f"</span></h3><div class='cols'><div>")
            if media.get("keyframes"):
                h.append(f"<img loading='lazy' src='{rel(media['keyframes'])}' alt='Keyframes {lab}'>")
            if media.get("overlay"):
                h.append(f"<details><summary>Overlay-Video (Zeitlupe ½)</summary>"
                         f"<video controls preload='none' src='{rel(media['overlay'])}'></video></details>")
            h.append("</div><div>")
            for f in a.get("findings", [])[:3]:
                h.append(f"<div class='finding'><b>{html.escape(f['title'])}</b>"
                         f"<small>{html.escape(f['detail'])}</small>"
                         f"<small>Drill: {html.escape(f['drill'])}</small></div>")
            if not a.get("findings"):
                h.append("<p class='ok'>Keine Auffälligkeiten über den Schwellwerten.</p>")
            h.append("<table>")
            for k, l, unit, band in METRICS:
                v = a["metrics"].get(k)
                if v is None:
                    continue
                bad = band and not (band[0] <= v <= band[1])
                h.append(f"<tr><td>{html.escape(l)}</td><td class='num{' flag' if bad else ''}'>"
                         f"{_fmt(v)} {unit}</td></tr>")
            units = s["mevo"].get("units", {})
            for k, l in MEVO:
                if s["mevo"].get(k) is not None:
                    h.append(f"<tr><td>Mevo+ {html.escape(l)}</td><td class='num'>"
                             f"{_fmt(s['mevo'][k])} {html.escape(units.get(k, ''))}</td></tr>")
            sc = a.get("scale", {})
            h.append("</table><p class='note'>Massstab: " + ", ".join(
                f"{v.upper()} {x.get('method')}" for v, x in sc.items()) + "</p>")
            for w in a.get("warnings", []):
                h.append(f"<p class='note flag'>{html.escape(w)}</p>")
            h.append("</div></div></section>")
    h.append("</main></body></html>")
    out.write_text("".join(h), encoding="utf-8")
    return out
