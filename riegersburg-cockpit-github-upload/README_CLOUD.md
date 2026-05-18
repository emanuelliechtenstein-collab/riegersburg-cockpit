# Riegersburg-Cockpit online nutzen

Ziel: Emanuel und Sonja können unabhängig voneinander arbeiten, auch wenn Emanuels Mac ausgeschaltet ist.

## Zielarchitektur

- App: Streamlit Community Cloud
- Daten: gehostete PostgreSQL-Datenbank, z. B. Supabase
- Login: gemeinsames App-Passwort über Streamlit-Secrets
- Dokumente: später über Cloud-Ordner oder Links, nicht mehr direkt vom Desktop

## Was vorbereitet ist

Die App erkennt automatisch:

- ohne `database_url`: lokale SQLite-Datei `data/riegersburg.db`
- mit `database_url`: Cloud-PostgreSQL-Datenbank

Beim ersten Cloud-Start werden vorhandene CSV-Startdaten in die Cloud-Datenbank übernommen.

## Benötigte Konten

1. GitHub-Konto
2. Streamlit Community Cloud Konto
3. Supabase-Konto oder eine andere PostgreSQL-Datenbank

## Streamlit-Secrets

In Streamlit Cloud bei der App unter **Secrets** eintragen:

```toml
database_url = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/postgres"
app_password = "EinSicheresGemeinsamesPasswort"
```

Die Datei `.streamlit/secrets.toml.example` zeigt das Format. Echte Passwörter gehören nie in GitHub.

## Deployment-Schritte

1. Dieses Projekt in ein privates GitHub-Repository hochladen.
2. In Streamlit Community Cloud eine neue App aus diesem Repository erstellen.
3. `app.py` als Hauptdatei auswählen.
4. Die Secrets eintragen.
5. App starten.
6. Link mit Sonja teilen.

## Datenübernahme

Leere CSV-Vorlagen liegen im Ordner `data/templates/`.

Die echten Projekt-CSV-Dateien werden aus Datenschutzgründen nicht in GitHub hochgeladen. Für die erste Datenübernahme gibt es zwei sichere Wege:

1. lokal die App mit `database_url` starten, damit sie direkt in Supabase migriert
2. die CSV-Dateien manuell in Supabase importieren

Die benötigten Tabellen heißen:

- `foerderstellen`
- `aufgaben`
- `kontakte`

Danach ist die Cloud-Datenbank die führende Datenquelle.

## Hinweis zu Dokumenten

Der bisherige Dokumenten-Tab kann lokale Ordner nur lesen, wenn die App auf Emanuels Mac läuft. In der Cloud gibt es keinen Zugriff auf den Desktop.

Für dauerhafte gemeinsame Dokumente empfiehlt sich:

- Google Drive Ordner mit Freigabelinks
- Dropbox/iCloud-Links
- oder eine spätere Erweiterung der App um Datei-Uploads
