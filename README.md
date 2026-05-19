# Riegersburg Sanierungsprogramm

Eine einfache lokale Web-App zur Steuerung eines Sanierungsprogramms mit Förderstellen, Ansprechpartnern, Aufgaben, Prioritäten, Dokumentenordner und kompaktem Berichtsexport.

## Funktionen

- Förderstellen verwalten
- Ansprechpartner und Kontakte verwalten
- offene Aufgaben und nächste Schritte verwalten
- Status je Förderlinie anzeigen
- einfache Priorisierung nach Dringlichkeit
- Projektbericht als Markdown oder PDF herunterladen
- dauerhafte Speicherung in `data/riegersburg.db`
- automatische Backups im Ordner `data/backups/`
- CSV-Export für Excel/Numbers im Ordner `data/`
- einfacher Passwortschutz für die gemeinsame Nutzung
- lokaler Sync-Helfer für Plaud-/Protokolldateien aus einem Mac-Ordner

## Installation

Voraussetzung: Python 3.10 oder neuer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Start

```bash
streamlit run app.py
```

Danach öffnet sich die App im Browser. Falls nicht, zeigt Streamlit eine lokale Adresse wie `http://localhost:8501` an.

## Start für gemeinsame Nutzung im WLAN

Am einfachsten per Doppelklick auf:

```text
start_app.command
```

Die App läuft dann auf diesem Mac und ist im selben WLAN erreichbar. Auf Sonjas Gerät muss die Adresse dieses Macs verwendet werden, zum Beispiel:

```text
http://192.168.0.25:8503
```

Das voreingestellte Passwort lautet:

```text
Riegersburg2030
```

Bitte das Passwort nach dem ersten Start in der Seitenleiste ändern.

## Daten

Die App speichert dauerhaft in:

- `data/riegersburg.db`

Zur Weitergabe oder Kontrolle können diese CSV-Dateien aus der App heraus aktualisiert werden:

- `data/foerderstellen.csv`
- `data/aufgaben.csv`
- `data/kontakte.csv`

Die CSV-Dateien können bei Bedarf mit Excel, Numbers oder einem Texteditor geöffnet werden. Änderungen sollten jedoch in der App erfolgen, weil die Datenbank die führende Datenquelle ist.

Die aktuell enthaltenen Startdaten wurden aus den kopierten Riegersburg-Projektunterlagen verdichtet. Eine kurze Importnotiz liegt unter `data/import_summary.md`.

## Hinweise

- Über den Button **Beispieldaten anlegen** können erste Einträge für den Start erzeugt werden.
- Die Dringlichkeit wird aus Priorität und Frist berechnet.
- Der PDF-Export nutzt das Paket `reportlab`, das in `requirements.txt` enthalten ist.

## Mac-Ordner-Sync

Für Protokolle, die laufend auf dem Mac abgelegt werden, gibt es den lokalen Helfer:

```text
sync_desktop_folder.command
```

Die Einrichtung ist in `SYNC_SETUP.md` beschrieben. Der Helfer überträgt neue TXT-/Markdown-/SRT-/VTT-/Word-/PDF-Dateien in die Cloud-Datenbank. In der Online-App erscheinen sie danach im Reiter **Synchronisierte Dateien**.
