#!/bin/bash
set -e

cd "$(dirname "$0")"

MAIL_SCRIPT_DIR="$HOME/Library/Application Scripts/com.apple.mail"
MAIL_SCRIPT_NAME="Riegersburg Mail Import.scpt"

mkdir -p "$MAIL_SCRIPT_DIR"
osacompile -o "$MAIL_SCRIPT_DIR/$MAIL_SCRIPT_NAME" mail_to_riegersburg.applescript

mkdir -p "$HOME/Desktop/Fortbestand der Riegerbsurg /Apple Mail Import"

echo ""
echo "Das Apple-Mail-Skript wurde installiert:"
echo "$MAIL_SCRIPT_DIR/$MAIL_SCRIPT_NAME"
echo ""
echo "Naechster Schritt:"
echo "Apple Mail öffnen > Einstellungen > Regeln > Regel hinzufügen"
echo "Dann als Aktion 'AppleScript ausführen' wählen und 'Riegersburg Mail Import.scpt' auswählen."
echo ""
read -n 1 -s -r -p "Fertig. Zum Schließen eine Taste drücken."
