#!/bin/bash
# Holt die neueste Version von GitHub und startet den Server neu. Daten bleiben unberührt.
set -u
cd "$(dirname "$0")"
KENNUNG="de.lueke-strahlt.baustelle"
echo "Neueste Version holen …"
git pull --ff-only || { echo "git pull ging nicht. Bitte Bescheid geben."; read -r -p "Enter … " _; exit 1; }
.venv/bin/python -m pip install -q -r requirements.txt
launchctl kickstart -k "gui/$(id -u)/$KENNUNG.server" && echo "Server neu gestartet."
sleep 2
curl -fs http://127.0.0.1:${LST_PORT:-8080}/api/leute >/dev/null && echo "✓ läuft" || echo "✗ antwortet nicht – siehe ~/LST-Baustelle-Daten/logs/server.log"
read -r -p "Enter zum Schließen … " _
