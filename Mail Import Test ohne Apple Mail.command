#!/bin/bash
set -e

cd "$(dirname "$0")"

TARGET="$HOME/Desktop/Fortbestand der Riegerbsurg /Apple Mail Import"
mkdir -p "$TARGET"

STAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
TESTFILE="$TARGET/${STAMP}_Mail_Import_Test.txt"

cat > "$TESTFILE" <<EOF
Quelle: Mail-Import-Test
Apple-Mail-ID: test-${STAMP}
Betreff: Test Mail Import Riegersburg
Von: Codex Test
Datum: $(date)
Importiert am: $(date)

Dies ist eine technische Testdatei fuer den Riegersburg-Mail-Import.
Wenn diese Datei in der App unter Dokumente & Auswertung erscheint,
funktioniert der lokale Ordner-Sync. Dann liegt ein verbleibendes Problem
nur noch bei der Uebernahme aus Apple Mail.

Naechster Schritt: Mail-Import fuer echte Projektmails pruefen.
EOF

echo ""
echo "Testdatei wurde angelegt:"
echo "$TESTFILE"
echo ""
echo "Synchronisierung startet jetzt..."
echo ""

.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python sync_desktop_folder.py

echo ""
echo "Bitte danach die App neu laden und unter 'Dokumente & Auswertung' nachsehen."
echo "Suchbegriff: Test Mail Import Riegersburg"
echo ""
read -n 1 -s -r -p "Fertig. Zum Schließen eine Taste drücken."
