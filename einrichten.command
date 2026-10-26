#!/bin/bash
# Einrichtung der LST Baustellen-App auf dem Mac mini.
# Doppelklick im Finder oder im Terminal:  ./einrichten.command
# Kann gefahrlos mehrfach laufen; vorhandene Daten bleiben unberührt.
set -u
cd "$(dirname "$0")"
APP="$(pwd)"
DATEN="${LST_DATEN:-$HOME/LST-Baustelle-Daten}"
PORT="${LST_PORT:-8080}"
KENNUNG="de.lueke-strahlt.baustelle"
AGENTS="$HOME/Library/LaunchAgents"
schritt() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()      { printf '  ✓ %s\n' "$1"; }
fehler()  { printf '  ✗ %s\n' "$1"; }

schritt "1/7  Python prüfen"
if ! xcode-select -p >/dev/null 2>&1; then
  fehler "Die Apple-Entwicklerwerkzeuge fehlen (bringen Python mit)."
  echo "     Es öffnet sich gleich ein Fenster „Installieren“. Danach dieses Skript noch einmal starten."
  xcode-select --install
  read -r -p "Enter zum Beenden … " _; exit 1
fi
PY=/usr/bin/python3
"$PY" -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null || { fehler "Python 3.9 oder neuer nötig."; exit 1; }
ok "$("$PY" --version)"

schritt "2/7  Programm-Pakete installieren (braucht Internet, 1–3 Minuten)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
if .venv/bin/python -m pip install -q -r requirements.txt; then ok "Pakete installiert"; else fehler "Installation fehlgeschlagen (Internet?)"; exit 1; fi
VPY="$APP/.venv/bin/python"

schritt "3/7  Daten-Ordner"
mkdir -p "$DATEN/logs"
export LST_DATEN="$DATEN"
"$VPY" -c "from lst import speicher; speicher.einrichten(); speicher.geheim()" && ok "$DATEN"
"$VPY" -c "import lst.server" && ok "Selbsttest bestanden"

schritt "4/7  PINs für das Büro"
"$VPY" -m lst.verwaltung liste
for wer in Andy Tina; do
  if "$VPY" -m lst.verwaltung liste | grep -E "^$wer " | grep -q "PIN fehlt"; then
    read -r -p "  PIN für $wer jetzt setzen? [J/n] " a
    [ "${a:-j}" = "n" ] || "$VPY" -m lst.verwaltung pin "$wer"
  fi
done
echo "  Die übrigen PINs vergibst du im Büro unter „Mitarbeiter“."

schritt "5/7  Autostart einrichten (Server und nächtliche Sicherung)"
mkdir -p "$AGENTS"
SPIEGEL_ZEILE=""
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs"
if [ -d "$ICLOUD" ]; then
  read -r -p "  Nachts zusätzlich nach iCloud Drive (Ordner „LST Baustelle Sicherung“) spiegeln? [J/n] " a
  if [ "${a:-j}" != "n" ]; then
    SPIEGEL_ZEILE="<key>LST_SPIEGEL</key><string>$ICLOUD/LST Baustelle Sicherung</string>"
    ok "iCloud-Spiegel an (Konto: $(defaults read MobileMeAccounts Accounts 2>/dev/null | awk -F'"' '/AccountID/{print $2; exit}'))"
  fi
fi
cat > "$AGENTS/$KENNUNG.server.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$KENNUNG.server</string>
  <key>ProgramArguments</key><array><string>$VPY</string><string>-m</string><string>lst.server</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>EnvironmentVariables</key><dict>
    <key>LST_DATEN</key><string>$DATEN</string><key>LST_PORT</key><string>$PORT</string><key>LST_HOST</key><string>127.0.0.1</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DATEN/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DATEN/logs/server.log</string>
</dict></plist>
PL
cat > "$AGENTS/$KENNUNG.sicherung.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$KENNUNG.sicherung</string>
  <key>ProgramArguments</key><array><string>$VPY</string><string>-m</string><string>lst.sicherung</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>EnvironmentVariables</key><dict><key>LST_DATEN</key><string>$DATEN</string>$SPIEGEL_ZEILE</dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$DATEN/logs/sicherung.log</string>
  <key>StandardErrorPath</key><string>$DATEN/logs/sicherung.log</string>
</dict></plist>
PL
for teil in server sicherung; do
  launchctl bootout "gui/$(id -u)/$KENNUNG.$teil" 2>/dev/null
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$KENNUNG.$teil.plist" && ok "$teil"
done

schritt "6/7  Server starten"
for i in $(seq 1 20); do curl -fs "http://127.0.0.1:$PORT/api/leute" >/dev/null && break; sleep 1; done
if curl -fs "http://127.0.0.1:$PORT/api/leute" >/dev/null; then ok "läuft auf http://127.0.0.1:$PORT"; else fehler "Server antwortet nicht. Protokoll: $DATEN/logs/server.log"; tail -20 "$DATEN/logs/server.log"; exit 1; fi

schritt "7/7  Zugang über Tailscale (HTTPS)"
TS=""
for k in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do command -v "$k" >/dev/null 2>&1 && { TS="$k"; break; }; [ -x "$k" ] && { TS="$k"; break; }; done
if [ -z "$TS" ]; then
  fehler "Tailscale nicht gefunden. Bitte Tailscale installieren, anmelden und dieses Skript erneut starten."
else
  if "$TS" serve --bg "$PORT" >/dev/null 2>&1; then
    ok "Tailscale leitet HTTPS an die App weiter"
  else
    fehler "„tailscale serve“ ging nicht. Meist fehlt in der Tailscale-Verwaltung (login.tailscale.com → DNS) „HTTPS Certificates“. Einschalten und Skript neu starten."
  fi
  ADR="$("$VPY" -m lst.verwaltung adresse)"
  echo
  echo "  Adresse fürs iPad (in Safari öffnen, dann „Teilen → Zum Home-Bildschirm“):"
  echo "     $ADR"
  echo "  Büro:  ${ADR%/app/}/buero"
fi

echo
echo "Fertig. Hinweise:"
echo "  • Der Server startet nach jedem Neustart automatisch, sobald dieser Benutzer angemeldet ist."
echo "    Für den Dauerbetrieb: Systemeinstellungen → Benutzer → automatische Anmeldung, Ruhezustand aus."
echo "  • Notion:      LST_DATEN=\"$DATEN\" .venv/bin/python -m lst.verwaltung notion"
echo "  • Mailversand: LST_DATEN=\"$DATEN\" .venv/bin/python -m lst.verwaltung mail"
echo "  • Daten liegen in $DATEN – nie im Programm-Ordner."
read -r -p "Enter zum Schließen … " _
