#!/bin/bash
# Holt die neueste Version von GitHub und startet die App neu. Daten bleiben unberührt.
# Im Container:              baustelle-aktualisieren
# Vom Proxmox-Rechner aus:   pct exec <Nummer> -- bash /opt/lst-baustelle/linux/aktualisieren.sh
set -uo pipefail
# Eigene Kopie ausführen: git pull ersetzt dieses Skript sonst mitten im Lauf
if [ "${LST_KOPIE:-}" != 1 ]; then cp "$0" /tmp/lst-aktualisieren.sh && LST_KOPIE=1 exec bash /tmp/lst-aktualisieren.sh "$@"; fi
APP=/opt/lst-baustelle
PORT="${LST_PORT:-8080}"
cd "$APP" || exit 1
VORHER="$(git rev-parse --short HEAD)"
echo "Neueste Version holen …"
git pull -q --ff-only || { echo "✗ git pull ging nicht. Bitte Bescheid geben."; exit 1; }
command -v pdftoppm >/dev/null || { apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq poppler-utils >/dev/null 2>&1 || echo "! poppler-utils ließ sich nicht installieren (Seitenansicht auf dem iPad)"; }
"$APP/.venv/bin/python" -m pip install -q -r requirements.txt || { echo "✗ Pakete ließen sich nicht installieren."; exit 1; }
install -m 755 "$APP/linux/aktualisieren.sh" /usr/local/bin/baustelle-aktualisieren
systemctl restart lst-baustelle.service
for _ in $(seq 1 20); do curl -fs "http://127.0.0.1:$PORT/api/leute" >/dev/null && break; sleep 1; done
if curl -fs "http://127.0.0.1:$PORT/api/leute" >/dev/null; then
  echo "✓ läuft – Stand $VORHER → $(git log -1 --format='%h vom %cd: %s' --date=format:'%d.%m.%Y')"
else
  echo "✗ antwortet nicht. Protokoll:"; journalctl -u lst-baustelle -n 30 --no-pager; exit 1
fi
