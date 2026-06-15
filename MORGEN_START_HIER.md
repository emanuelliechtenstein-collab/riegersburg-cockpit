# Morgen: Mail-Import ruhig testen

Bitte morgen nicht bei Apple Mail beginnen, sondern zuerst den Sync selbst testen.

## Schritt 1: Sync ohne Apple Mail testen

1. Im Projektordner doppelklicken:
   `Mail Import Test ohne Apple Mail.command`
2. Schwarzes Fenster bis `Fertig` laufen lassen.
3. App neu laden.
4. In der App öffnen:
   `Dokumente & Auswertung`
5. Dort nachsehen oder suchen:
   `Test Mail Import Riegersburg`

Wenn diese Testdatei erscheint, funktioniert der Weg vom Mac-Ordner in die App.

## Schritt 2: Erst danach echte Mail testen

1. Apple Mail öffnen.
2. Eine einzelne Projektmail markieren.
3. Im Projektordner doppelklicken:
   `Markierte Mail ins Cockpit übernehmen.command`
4. Schwarzes Fenster bis `Fertig` laufen lassen.
5. App neu laden.
6. In `Dokumente & Auswertung` nach dem Betreff der Mail suchen.

## Auswertung

- Testdatei erscheint, echte Mail nicht:
  Problem liegt bei Apple-Mail-Zugriff oder macOS-Berechtigung.
- Testdatei erscheint nicht:
  Problem liegt beim lokalen Sync oder bei der Datenbankverbindung.
- Beides erscheint:
  Dann funktioniert der Mail-Import grundsätzlich.

## Wichtig

Die automatische Apple-Mail-Regel erst einrichten, wenn der manuelle Test funktioniert.
