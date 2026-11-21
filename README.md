# SwingLab – Golfschwung-Analyse mit 2 Webcams + Mevo+

Automatische Schwunganalyse für den Golfsimulator: zwei Webcams (Down-the-Line und Face-On) nehmen
jeden Schlag auf, sobald das Treffgeräusch erkannt wird. Pose-Schätzung (MediaPipe, 33 Körperpunkte)
liefert Schwungphasen und Kennzahlen. Die Mevo+-Schlagdaten werden zugeordnet und alles
landet in einem HTML-Report mit Verlauf und Korrelationen Körper ↔ Ballflug.

```
 Mikrofon ──► Impact erkannt ──► Ringpuffer beider Kameras ──► dtl.mp4 + fo.mp4 (synchron)
                                                                     │
         Mevo+ CSV (FS Golf) ──────────────────┐                     ▼
                                               │        Pose (MediaPipe) → Phasen → Kennzahlen
                                               ▼                     │
                                   report.html ◄── Feedback + Drills ◄┘ + Overlay-Video + Keyframes
```

## SwingLab – die Desktop-App (empfohlen)

Aufnahme, Schwungdaten und Analyse in **einer** Software mit vier Bereichen:

| Bereich | Inhalt |
|---|---|
| **Aufnahme** | Live-Bild beider Kameras, Mikrofonpegel, «Session starten». Jeder Schlag wird automatisch gespeichert und im Hintergrund analysiert. «Letzter Swing» zeigt Keyframes, Kennzahlen, Mevo+-Werte und den wichtigsten Verbesserungspunkt mit Drill. |
| **Analyse** | Alle Sessions/Swings. Synchroner Player DTL + Face-On mit Skelett und Referenzlinien, Zeitlupe (1/8× bis 1×), Einzelbild, Sprung zu Address/Top/Impact usw., Feedback, Kennzahlen, Mevo+-Daten. Mevo+-CSV- und Video-Import. |
| **Verlauf** | Grösste Baustellen, alle Kennzahlen über die Zeit (Filter nach Zeitraum/Schläger), Korrelation Körper ↔ Ballflug, HTML-Report. |
| **Einstellungen** | Golfer, Kameras (Auflösung, fps, Belichtung, Treiberdialog), Kalibrierung per Klick ins Live-Bild, Audio-Trigger, Mevo+ live. |

### Installation

**Variante A – fertige .exe (kein Python nötig):**
1. Auf GitHub → *Actions* → «Windows-App bauen» → letzten grünen Lauf öffnen → Artefakt **SwingLab-Windows** herunterladen.
2. ZIP entpacken (z.B. nach `C:\Programme\SwingLab`) und `SwingLab.exe` starten. Die MediaPipe-Modelle sind eingebaut.
3. Daten und Einstellungen liegen in `Dokumente\SwingLab` (anderer Ort: `SwingLab.exe --home D:\Golf`).

Falls Windows SmartScreen warnt («Unbekannter Herausgeber»): *Weitere Informationen → Trotzdem ausführen*
(die App ist nicht signiert).

**Variante B – aus dem Quellcode:**
```powershell
git clone https://github.com/corof/test.git swinglab; cd swinglab
py -3.12 -m venv .venv; .venv\Scripts\activate
pip install -e .
swinglab-app
```
Optional `winget install Gyan.FFmpeg`: Damit sind die Overlay-Videos auch im HTML-Report abspielbar.

### Erster Start
1. **Einstellungen:** Kamera-Nummern eintragen (0, 1 …; DTL und FO ggf. tauschen), Auflösung 1920×1080,
   60 fps, Belichtung −9. Mikrofon wählen. «Übernehmen». Im Aufnahme-Tab beide Bilder prüfen.
2. **Kalibrieren:** Stab bekannter Länge in die Ebene des Golfers halten → «Kalibrieren …» → beide Enden
   anklicken.
3. **Aufnahme → Session starten** und schlagen. Der Mikrofonpegel muss beim Treffer über die orange
   Marke gehen (sonst in den Einstellungen «Mindestpegel» senken).
4. **Mevo+-Daten:**
   - *Live:* Unterstützt deine Sim-Software/der Mevo+-Connector das GSPro-Open-Connect-Protokoll, in
     den Einstellungen «Mevo+ live» aktivieren. Jeder Schuss wird dann automatisch dem Swing zugeordnet
     (Relais-Modus: weiter an GSPro).
   - *Sonst:* nach der Session in der FS Golf App als CSV exportieren → Analyse → «Mevo+-CSV importieren».

## Kommandozeile (Alternative / Automatisierung)

```powershell
pip install -e .
swinglab init          # legt config.toml an
```
Das MediaPipe-Modell (~30 MB) wird bei der ersten Analyse automatisch geladen.

## Kameras einrichten (Razer Kiyo Pro)

| Einstellung | Wert | Warum |
|---|---|---|
| HDR | **aus** (Razer Synapse) | Mit HDR liefert die Kiyo Pro nur 30 fps |
| Auflösung / fps | 1920×1080 @ 60, MJPG | Maximum der Kiyo Pro; MJPG ist für 60 fps nötig |
| Belichtung | **manuell, möglichst kurz** (`exposure = -9` ≈ 1/500 s) | Gegen Bewegungsunschärfe. Dafür braucht es **viel Licht** (z.B. 2× LED-Fluter ≥ 50 W, flimmerfrei) |
| Autofokus | aus | Fokus springt sonst beim Schwung |
| Sichtfeld | Golfer ganz im Bild inkl. Schläger am Top und Finish | |

```powershell
swinglab cams              # zeigt Kamera-Indizes + Mikrofone -> in config.toml eintragen
swinglab settings dtl      # Treiberdialog (Belichtung/Gain/Fokus) mit Live-Bild
swinglab capture --preview # Vorschau beider Kameras + Audio-Pegel (q beendet)
```

**Positionierung**
- **DTL (von hinten):** auf Hand- bzw. Hüfthöhe (~1.0–1.2 m), 3–4 m hinter dem Ball, **genau auf der Ziellinie** bzw. parallel dazu durch die Hände. Kamera waagrecht.
- **Face-On (seitlich):** auf Brusthöhe (~1.2 m), rechtwinklig zur Ziellinie, auf den Punkt zwischen den Füssen zielend.
- Beide Kameras **fix montieren**. Nur dann sind Werte über Wochen vergleichbar.

**Massstab kalibrieren** (einmalig pro Kameraposition, sonst Schätzung aus der Körpergrösse ±10 %):
Einen Stab bekannter Länge (z.B. 100 cm) **in die Ebene des Golfers** halten bzw. legen, dann:
```powershell
swinglab calibrate fo 100
swinglab calibrate dtl 100
```

## Ablauf einer Session

```powershell
swinglab capture
```
- Einfach schlagen. Das Treffgeräusch löst aus und speichert 2.5 s vor bis 1.5 s nach dem Impact
  aus beiden Kameras, zeitlich synchronisiert. Funktioniert der Audio-Trigger nicht: **Enter**
  kurz nach dem Schlag drücken.
- Jeder Swing wird im Hintergrund analysiert; in der Konsole erscheint sofort Tempo und Hauptpunkt.
- Mikrofon wählen: `[audio] device = "Kiyo"` (Teil des Namens). Empfindlichkeit über `threshold_factor` / `min_peak`.

Danach die **Mevo+-Daten** dazunehmen:
1. FS Golf App → Session → Daten exportieren (CSV, z.B. per Mail an den PC).
2. `swinglab mevo C:\Pfad\export.csv`. Die Zuordnung zur letzten Session erfolgt über die Uhrzeit (±30 s).
   - Handy- und PC-Uhr müssen stimmen. Hat das CSV keine Uhrzeit pro Schuss, wird nach Reihenfolge
     zugeordnet; Versatz mit `--offset 1` usw.
   - Mevo+ der 1. Generation liefert Club Path / Face nur mit dem **Pro Package**. Fehlende Spalten werden
     einfach ignoriert.
   - Der Parser ist tolerant (Einheiten, «L/R», Dezimalkomma, `;`). Erkennt er dein Export-Format nicht:
     erste Zeilen des CSV schicken, dann wird das Mapping ergänzt (`swinglab/mevo.py`, `ALIASES`).

Report: `data/report.html` im Browser öffnen (`swinglab report` erzeugt ihn neu).

Videos aus anderen Quellen (Handy, OBS …): `swinglab import --dtl a.mp4 --fo b.mp4`
(mit Tonspur wird der Impact automatisch gefunden).

## Kennzahlen

Vorzeichen: **Face-On** `+` = Richtung Ziel · **DTL** `+` = Richtung Ball · vertikal `+` = nach oben ·
Rotation `+` = Rückschwung-Richtung. Rechts-/Linkshänder und gespiegelte Kameras werden automatisch erkannt
(`[golfer] handedness` setzen).

| Kennzahl | Ansicht | Bedeutung | Richtwert |
|---|---|---|---|
| `tempo_ratio` | beide | Rückschwung- : Abschwungdauer | ~3 : 1 |
| `head_sway_top_cm` / `_impact_cm` | FO | seitliche Kopfbewegung | Impact ≤ +5 cm |
| `head_vertical_impact_cm` | FO | Kopf hoch/runter bis Impact | ±5 cm |
| `hip_sway_top_cm` | FO | Becken seitlich am Top (Sway = stark negativ) | > −8 cm |
| `hip_slide_impact_cm` | FO | Becken Richtung Ziel bei Impact (Gewichtsverlagerung) | ≥ +2 cm |
| `early_extension_cm` | DTL | Becken Richtung Ball bei Impact | ≤ 5 cm |
| `spine_angle_change_impact_deg` | DTL | Verlust der Vorneigung (negativ = Aufrichten) | > −6° |
| `trail_knee_change_top_deg` | DTL | hinteres Knie streckt im Rückschwung | < 15° |
| `shoulder_turn_top_deg`, `hip_turn_top_deg`, `x_factor_top_deg`, `hip_open_impact_deg` | 3D* | Rotationen | Trend |
| `pelvis_lead_ms` | 3D* | Becken erreicht max. Drehgeschwindigkeit vor den Schultern | > 0 |
| `lead_elbow_top_deg` | 3D* | Streckung Führungsarm am Top | ≥ 145° |

\* aus MediaPipe-3D-Weltkoordinaten einer einzelnen Kamera: gut für **Trends**, absolut ±10° ungenau.

Schwellwerte für das Feedback stehen in `config.toml` unter `[thresholds]`.

## Grenzen und wie man sie verschiebt

- **60 fps:** Bei 100 mph legt der Schlägerkopf zwischen zwei Frames ~75 cm zurück. Körperbewegungen
  sind präzise messbar; Schlägerblatt und Shaft um den Impact nicht. Dafür ist der Mevo+ da.
- **Bewegungsunschärfe** ist der grösste Feind der Pose-Erkennung: Licht hoch, Belichtung kurz.
- **Kleidung:** eng anliegend und mit Kontrast zum Hintergrund verbessert die Erkennung deutlich.
- Weite Bewegungen der Hände im Abschwung werden bei Unschärfe teils ungenau. Das Tool interpoliert
  kurze Lücken und markiert fehlende Werte mit «–».

## Entwicklung

```bash
pip install -e .[dev]
pytest            # Tests laufen mit synthetischen Schwüngen, ohne Kamera (inkl. GUI offscreen)
```

Struktur: `app/` (Desktop-App, PySide6) · `capture.py` (Aufnahme-Engine/Ringpuffer) · `shots.py`
(Mevo+ live via Open Connect, Zuordnung) · `store.py` (Datenablage) · `audio.py` (Impact-Erkennung) · `pose.py` (MediaPipe,
Glättung) · `analysis.py` (Phasen + Kennzahlen) · `feedback.py` (Regeln + Drills) · `overlay.py`
(Video/Keyframes) · `mevo.py` (CSV-Import + Zuordnung) · `report.py` (HTML) · `cli.py`.

**Ideen für die nächsten Schritte:** Schlägererkennung (Shaft-Plane im DTL) · Sprachausgabe des
Hauptpunkts direkt nach dem Schlag · Live-Daten vom Mevo+ statt CSV · Vergleich mit einem
Referenz-Swing (Overlay «bester Swing» vs. aktueller).
