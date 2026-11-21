"""Regelbasiertes Feedback mit Priorisierung und Drills (Deutsch)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Finding:
    key: str
    title: str
    detail: str
    drill: str
    severity: float  # >1 = Schwelle überschritten; höher = wichtiger

    def as_dict(self):
        return self.__dict__.copy()


def _v(m, k):
    v = m.get(k)
    return None if v is None else float(v)


def evaluate(m: dict, th: dict) -> list[Finding]:
    out: list[Finding] = []

    def add(key, value, limit, title, detail, drill):
        if value is not None and limit and value > limit:
            out.append(Finding(key, title, detail.format(v=value, l=limit), drill, value / limit))

    # --- Face-On ---------------------------------------------------------
    hs = _v(m, "head_sway_impact_cm")
    add("head_sway_impact", hs, th["head_sway_impact_cm"],
        "Kopf wandert bis zum Treffmoment Richtung Ziel",
        "Kopf bei Impact {v:.1f} cm vor der Address-Position (Grenze {l:.0f} cm). "
        "Folge oft: steiler Eintreffwinkel, Pull/Slice, dünne Treffer.",
        "Alignment-Stick senkrecht vor der Ziel-Seite des Kopfes; Kopf bleibt bis nach dem "
        "Treffer dahinter. Halbe Schwünge mit Fokus 'hinter dem Ball bleiben'.")
    hst = _v(m, "head_sway_top_cm")
    add("head_sway_top", None if hst is None else -hst, th["head_sway_top_cm"],
        "Kopf weicht im Rückschwung stark vom Ziel weg",
        "Kopf am Top {v:.1f} cm vom Ziel weg (Grenze {l:.0f} cm).",
        "Rückschwung vor dem Spiegel/der FO-Kamera: Kopf ruhig, Brust dreht über dem "
        "hinteren Bein statt seitlich zu schieben.")
    hsw = _v(m, "hip_sway_top_cm")
    add("hip_sway_top", None if hsw is None else -hsw, th["hip_sway_top_cm"],
        "Hüft-Sway im Rückschwung",
        "Becken am Top {v:.1f} cm vom Ziel weg verschoben (Grenze {l:.0f} cm) – "
        "Rotation wird durch seitliches Schieben ersetzt.",
        "Alignment-Stick (oder Stuhllehne) an der Aussenseite der hinteren Hüfte: im "
        "Rückschwung nicht berühren, Hüfte dreht nach hinten statt zur Seite.")
    sl = _v(m, "hip_slide_impact_cm")
    lim = th["hip_slide_impact_min_cm"]
    if sl is not None and sl < lim:
        out.append(Finding(
            "weight_shift", "Zu wenig Verlagerung zum Ziel",
            f"Becken bei Impact nur {sl:.1f} cm Richtung Ziel (Soll ≥ {lim:.0f} cm). "
            "Typisch: Hängen auf dem hinteren Fuss, fette/getoppte Treffer, hoher Ballflug.",
            "Step-Drill: Füsse zusammen, im Rückschwung mit dem vorderen Fuss Richtung Ziel "
            "auftreten und von dort den Abschwung starten.",
            1 + (lim - sl) / max(lim, 1)))
    hv = _v(m, "head_vertical_impact_cm")
    if hv is None:
        hv = _v(m, "head_vertical_impact_dtl_cm")
    if hv is not None and abs(hv) > th["head_vertical_cm"]:
        up = hv > 0
        out.append(Finding(
            "head_vertical", "Kopf hebt sich bis Impact" if up else "Kopf sinkt bis Impact",
            f"Kopfhöhe bei Impact {hv:+.1f} cm gegenüber Address (Grenze ±{th['head_vertical_cm']:.0f} cm).",
            "Höhe halten: Kopf an eine Schaumstoffnudel/Stange darüber bzw. davor anlehnen "
            "und langsame Schwünge machen." if up else
            "Weniger 'Abtauchen': Knie- und Hüftbeugung im Abschwung beibehalten statt tiefer gehen.",
            abs(hv) / th["head_vertical_cm"]))

    # --- Down-the-Line ---------------------------------------------------
    ee = _v(m, "early_extension_cm")
    add("early_extension", ee, th["early_extension_cm"],
        "Early Extension (Becken schiebt zum Ball)",
        "Becken bei Impact {v:.1f} cm näher am Ball als bei Address (Grenze {l:.0f} cm). "
        "Folge: Hände blockieren, Push/Hook, Shanks, inkonstante Kontakte.",
        "Gesäss bei Address an Stuhl/Wand; im Abschwung bleibt das Gesäss in Kontakt "
        "(vorderer Gesässbacken gleitet entlang der Wand).")
    sc = _v(m, "spine_angle_change_impact_deg")
    add("spine_loss", None if sc is None else -sc, th["spine_angle_loss_deg"],
        "Oberkörper richtet sich bis Impact auf",
        "Vorneigung verliert {v:.1f}° bis Impact (Grenze {l:.0f}°).",
        "Schläger quer vor der Brust: Pivot-Drehungen mit konstanter Vorneigung, "
        "dann halbe Schwünge mit gleichem Gefühl.")
    kn = _v(m, "trail_knee_change_top_deg")
    add("trail_knee", kn, th["trail_knee_extension_deg"],
        "Hinteres Knie streckt sich im Rückschwung",
        "Hinteres Knie öffnet sich um {v:.0f}° bis zum Top (Grenze {l:.0f}°) – "
        "Becken dreht zu stark/hoch, Sway Richtung Ziel (Reverse Pivot) möglich.",
        "Ball unter die Aussenkante des hinteren Fusses legen: Druck auf der Innenseite "
        "halten, Knieflexion bis zum Top behalten.")

    # --- Tempo / Arme -----------------------------------------------------
    tr = _v(m, "tempo_ratio")
    if tr is not None:
        if tr < th["tempo_min"]:
            out.append(Finding("tempo", "Rückschwung im Verhältnis zu schnell",
                               f"Tempo {tr:.1f}:1 (Ziel ca. 3:1).",
                               "Metronom/Tempo-App auf 3:1 (z.B. 21/7 Frames bei 60 fps "
                               "≈ 0.70 s / 0.23 s); Rückschwung bewusst 'voll' werden lassen.",
                               th["tempo_min"] / max(tr, 0.1)))
        elif tr > th["tempo_max"]:
            out.append(Finding("tempo", "Rückschwung im Verhältnis zu langsam",
                               f"Tempo {tr:.1f}:1 (Ziel ca. 3:1).",
                               "Rückschwung flüssiger, ohne Pause am Top; Metronom auf 3:1.",
                               tr / th["tempo_max"]))
    le = _v(m, "lead_elbow_top_deg")
    if le is not None and le < th["lead_elbow_top_min_deg"]:
        out.append(Finding("lead_arm", "Führungsarm am Top stark gebeugt",
                           f"Ellbogenwinkel ca. {le:.0f}° (Schätzung aus 3D-Pose).",
                           "Mehr Breite: Hände im Rückschwung weit weg von der Brust führen; "
                           "Schwungradius halten (Drill: Handtuch unter dem Führungsarm).",
                           th["lead_elbow_top_min_deg"] / max(le, 1)))
    out.sort(key=lambda f: f.severity, reverse=True)
    return out
