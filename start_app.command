#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install -r requirements.txt

echo ""
echo "Riegersburg-Cockpit startet..."
echo "Auf diesem Mac: http://127.0.0.1:8503"
echo "Für Sonja im selben WLAN: http://DEINE-MAC-IP:8503"
echo ""

HOME="$PWD" .venv/bin/streamlit run app.py \
  --server.address 0.0.0.0 \
  --server.port 8503 \
  --server.headless true \
  --browser.gatherUsageStats false
