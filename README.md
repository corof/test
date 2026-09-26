# KM271 (Buderus Logamatic 2107) – Heizung & Warmwasser ausschalten

Home-Assistant-Package für eine per KM271 angebundene Logamatic 2107.
Fügt zwei Schalter (**Heizung aktiv**, **Warmwasser aktiv**) und einen
**Alles AUS**-Knopf hinzu.

## Wie „aus“ technisch umgesetzt wird

Die Logamatic 2107 kennt über KM271 keinen echten „Aus“-Befehl. Es werden
die Mittel der Regelung selbst genutzt – der Frostschutz bleibt dadurch aktiv:

| Funktion        | Aktion                                                                 |
|-----------------|------------------------------------------------------------------------|
| Heizung aus     | Sommer/Winter-Schwelle HK1 → **9 = dauerhaft Sommer**, HK1-Betriebsart → Nacht |
| Warmwasser aus  | WW-Betriebsart → **Nacht** (keine Warmwasserladung)                    |
| wieder ein      | vorherige Werte werden aus `input_text`-Backups zurückgeschrieben      |

## Installation

1. Ordner `packages/` nach `/config/packages/` kopieren (File editor / Studio Code Server Add-on).
2. In `/config/configuration.yaml` ergänzen (falls noch nicht vorhanden):
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. **Entity-IDs prüfen** – oben im Script `heizung_aus` (Block `KONFIGURATION`)
   und in den beiden Template-Schaltern unten in der Datei. Die richtigen IDs
   findest du unter *Einstellungen → Geräte & Dienste → Entitäten*, Suche
   „Heizung Verscio“. Benötigt werden:
   - HK1 Betriebsart (select)
   - HK1 Sommer/Winter-Schwelle bzw. „Sommer ab“ (number oder select)
   - WW Betriebsart (select)
   
   Auch die Options-Texte (`Nacht`, `Auto`, …) müssen exakt wie im Dropdown lauten.
4. *Entwicklerwerkzeuge → YAML → Konfiguration prüfen*, dann neu starten.
5. Karte aus `dashboard/km271_karte.yaml` manuell ins Dashboard einfügen.

## Hinweise

- Einstellungen werden im EEPROM der Logamatic gespeichert – nicht im
  Minutentakt per Automation hin- und herschalten.
- Falls die Sommer-Schwelle in deiner Integration nicht als schreibbare
  Entität existiert, muss sie im ESPHome/KM271-Firmware-YAML freigeschaltet werden.
