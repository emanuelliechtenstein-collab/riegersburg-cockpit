#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python sync_desktop_folder.py

echo ""
read -n 1 -s -r -p "Fertig. Zum Schließen eine Taste drücken."
