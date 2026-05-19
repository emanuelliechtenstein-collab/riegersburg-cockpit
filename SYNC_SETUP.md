# Mac-Ordner mit der Online-App synchronisieren

Mit diesem kleinen Helfer kannst du Protokolle weiterhin wie gewohnt in einem Ordner auf deinem Mac ablegen. Der Helfer uebertraegt neue Textdateien in die Cloud-Datenbank. Die Online-App zeigt sie danach im Reiter **Synchronisierte Dateien** an.

## Geeignete Dateien

Der Sync-Helfer unterstuetzt:

- `.txt`
- `.md`
- `.srt`
- `.vtt`
- `.docx`
- `.pdf`

Plaud-Exporte funktionieren am besten als TXT, SRT oder VTT. Word-Dateien werden ebenfalls gelesen. PDFs funktionieren, wenn der Text im PDF wirklich als Text enthalten ist und nicht nur als Scan-Bild.

## Einmalige Einrichtung

1. Der gefundene Riegersburg-Ordner auf diesem Mac ist:

```text
/Users/rauby/Desktop/Fortbestand der Riegerbsurg 
```

2. Beim ersten Doppelklick auf `sync_desktop_folder.command` fragt der Helfer nach dem Supabase-Link und legt diese Datei automatisch an:

```text
sync_config.json
```

3. Fuege bei der Frage `database_url` den Supabase-Link ein. Er sieht ungefaehr so aus:

```text
postgresql://postgres.kwsnfijzutiuughccfgi:DEIN-PASSWORT@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
```

4. Bei der Frage nach dem Ordnerpfad einfach Enter druecken. Der richtige Ordner ist bereits vorgeschlagen.

Wichtig: `sync_config.json` enthaelt dein Datenbank-Passwort. Diese Datei nicht in GitHub hochladen. Die `.gitignore` verhindert das normalerweise automatisch.

## Nutzung

1. Neue Plaud- oder Protokolldatei in deinen Desktop-Ordner legen.

2. Doppelklick auf:

```text
sync_desktop_folder.command
```

3. Danach die Online-App oeffnen und den Reiter **Synchronisierte Dateien** verwenden.

Der Helfer erkennt bereits uebertragene Dateien und laedt sie nicht doppelt hoch.
