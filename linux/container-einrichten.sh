#!/bin/bash
# Richtet die LST Baustellen-App in einem Debian-Container ein (läuft als root im Container).
# Wird von proxmox-einrichten.sh aufgerufen, geht aber auch allein:
#   bash /opt/lst-baustelle/linux/container-einrichten.sh
# Kann gefahrlos mehrfach laufen; vorhandene Daten bleiben unberührt.
set -uo pipefail

APP=/opt/lst-baustelle
DATEN="${LST_DATEN:-/var/lib/lst-baustelle}"
PORT="${LST_PORT:-8080}"
NUTZER=lst
VPY="$APP/.venv/bin/python"
export DEBIAN_FRONTEND=noninteractive

schritt() { printf '\n  \033[1m%s\033[0m\n' "$1"; }
ok()      { printf '    ✓ %s\n' "$1"; }
hinweis() { printf '    ! %s\n' "$1"; }
abbruch() { printf '    ✗ %s\n' "$1"; exit 1; }
alsapp()  { runuser -u "$NUTZER" -- env LST_DATEN="$DATEN" "$@"; }

[ "$(id -u)" = 0 ] || abbruch "Bitte als root starten."
[ -f "$APP/requirements.txt" ] || abbruch "Programm fehlt in $APP."
cd "$APP" || exit 1   # „python -m lst…“ braucht den Programm-Ordner als Arbeitsordner

schritt "a) Pakete"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip fonts-dejavu-core sqlite3 curl git ca-certificates poppler-utils >/dev/null \
  || abbruch "Pakete ließen sich nicht installieren."
# Verdana wie in den Lexware-Belegen (Microsoft-Schriften aus Debian „contrib“). Ohne: DejaVu Sans.
if [ ! -f /usr/share/fonts/truetype/msttcorefonts/Verdana.ttf ]; then
  grep -rqsE '^deb .* contrib' /etc/apt/sources.list /etc/apt/sources.list.d/ \
    || echo "deb http://deb.debian.org/debian $(. /etc/os-release; echo "$VERSION_CODENAME") contrib" > /etc/apt/sources.list.d/contrib.list
  echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections
  apt-get update -qq && apt-get install -y -qq ttf-mscorefonts-installer >/dev/null 2>&1 || true
fi
[ -f /usr/share/fonts/truetype/msttcorefonts/Verdana.ttf ] && ok "Schrift Verdana" || hinweis "Verdana nicht verfügbar, Dokumente nutzen DejaVu Sans"
ok "$(python3 --version)"

schritt "b) Programm-Pakete (1–3 Minuten)"
[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r "$APP/requirements.txt" || abbruch "Installation fehlgeschlagen (Internet?)."
ok "installiert"

schritt "c) Benutzer und Daten-Ordner"
id "$NUTZER" >/dev/null 2>&1 || useradd --system --home-dir "$DATEN" --shell /usr/sbin/nologin "$NUTZER"
mkdir -p "$DATEN/logs"
chown -R "$NUTZER:$NUTZER" "$DATEN"
chmod 750 "$DATEN"
alsapp "$VPY" -c "from lst import speicher; speicher.einrichten(); speicher.geheim()" || abbruch "Daten-Ordner ließ sich nicht einrichten."
(alsapp "$VPY" -c "import lst.server") >/dev/null || abbruch "Selbsttest fehlgeschlagen."
ok "$DATEN (gehört Benutzer „$NUTZER“), Selbsttest bestanden"

schritt "d) PINs für das Büro"
(alsapp "$VPY" -m lst.verwaltung liste)
for wer in Andy Tina; do
  if (alsapp "$VPY" -m lst.verwaltung liste) | grep -E "^$wer " | grep -q "PIN fehlt"; then
    read -r -p "    PIN für $wer jetzt setzen? [J/n] " a
    [ "${a:-j}" = "n" ] || (alsapp "$VPY" -m lst.verwaltung pin "$wer")
  fi
done
echo "    Die übrigen PINs vergibst du im Büro unter „Mitarbeiter“."

schritt "e) Autostart (Server und nächtliche Sicherung der Datenbank)"
cat > /etc/systemd/system/lst-baustelle.service <<EOF
[Unit]
Description=LST Baustellen-App
After=network-online.target
Wants=network-online.target

[Service]
User=$NUTZER
WorkingDirectory=$APP
Environment=LST_DATEN=$DATEN LST_PORT=$PORT LST_HOST=127.0.0.1 PYTHONUNBUFFERED=1
ExecStart=$VPY -m lst.server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/lst-sicherung.service <<EOF
[Unit]
Description=LST Baustellen-App: Sicherung der Datenbank

[Service]
Type=oneshot
User=$NUTZER
WorkingDirectory=$APP
Environment=LST_DATEN=$DATEN
ExecStart=$VPY -m lst.sicherung
EOF
cat > /etc/systemd/system/lst-sicherung.timer <<EOF
[Unit]
Description=LST Baustellen-App: Sicherung jede Nacht 2:30 Uhr

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
install -m 755 "$APP/linux/aktualisieren.sh" /usr/local/bin/baustelle-aktualisieren
systemctl daemon-reload
systemctl enable -q lst-baustelle.service lst-sicherung.timer
systemctl restart lst-baustelle.service
systemctl start lst-sicherung.timer
for _ in $(seq 1 20); do curl -fs "http://127.0.0.1:$PORT/api/leute" >/dev/null && break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/api/leute" >/dev/null || { journalctl -u lst-baustelle -n 30 --no-pager; abbruch "Server antwortet nicht."; }
ok "Server läuft und startet mit dem Container"

schritt "f) Zugang über Tailscale (HTTPS für die iPads)"
if [ ! -c /dev/net/tun ]; then
  hinweis "/dev/net/tun fehlt im Container. Auf dem Proxmox-Rechner proxmox-einrichten.sh nutzen, das trägt es ein."
  exit 0
fi
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1 || abbruch "Tailscale ließ sich nicht installieren."
fi
systemctl enable -q --now tailscaled
if ! tailscale status >/dev/null 2>&1; then
  echo "    Gleich erscheint ein Link. Ihn am MacBook öffnen und mit dem Firmen-Konto bei Tailscale anmelden"
  echo "    (dasselbe Konto wie auf den iPads). Danach geht es hier von selbst weiter."
  tailscale up --hostname=baustelle || abbruch "Tailscale-Anmeldung abgebrochen. Skript einfach neu starten."
fi
ok "Tailscale angemeldet"
if tailscale serve --bg "$PORT" >/dev/null 2>&1; then
  ok "HTTPS leitet an die App weiter"
else
  hinweis "„tailscale serve“ ging nicht. Meist fehlt unter login.tailscale.com › DNS „HTTPS Certificates“."
  hinweis "Einschalten und dann:  tailscale serve --bg $PORT"
fi
ADR="$(LST_DATEN="$DATEN" "$VPY" -m lst.verwaltung adresse)"
echo
echo "    Adresse fürs iPad (in Safari öffnen, dann „Teilen › Zum Home-Bildschirm“):"
echo "       $ADR"
echo "    Büro:  ${ADR%/app/}/buero"
echo
echo "    Notion-Schlüssel eintragen:  runuser -u $NUTZER -- env LST_DATEN=$DATEN $VPY -m lst.verwaltung notion"
echo "    Mailversand eintragen:       runuser -u $NUTZER -- env LST_DATEN=$DATEN $VPY -m lst.verwaltung mail"
echo "    (beides im Ordner $APP ausführen)"
