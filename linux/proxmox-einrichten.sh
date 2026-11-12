#!/bin/bash
# Legt auf dem Proxmox-Rechner einen eigenen Container „baustelle“ an und richtet darin
# die LST Baustellen-App ein (Debian 12, getrennt von ioBroker).
#
# Aufruf auf dem Proxmox-Rechner als root, z. B. über  ssh root@192.168.188.20 :
#   bash <(curl -fsSL https://raw.githubusercontent.com/andylueke-lst/LST-Baustellenapp-v0.2/main/linux/proxmox-einrichten.sh)
#
# Kann gefahrlos mehrfach laufen: Ein vorhandener Container „baustelle“ wird weiterverwendet,
# Daten darin bleiben unberührt. Andere VMs und Container (ioBroker) werden nicht angefasst.
set -uo pipefail

REPO="${LST_REPO:-https://github.com/andylueke-lst/LST-Baustellenapp-v0.2.git}"
ZWEIG="${LST_ZWEIG:-main}"
NAME="baustelle"
KERNE=2
RAM=2048          # MB
SWAP=512          # MB
PLATTE=100        # GB auf der SSD
SPEICHER="${LST_SPEICHER:-local-lvm}"
SICHERUNG="${LST_SICHERUNG:-macmini-backup}"
# Spracheinstellung des Macs nicht in den Container durchreichen (sonst Locale-Warnungen)
export LANG=C.UTF-8 LC_ALL=C.UTF-8
unset LANGUAGE

schritt() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()      { printf '  ✓ %s\n' "$1"; }
hinweis() { printf '  ! %s\n' "$1"; }
abbruch() { printf '  ✗ %s\n' "$1"; exit 1; }

[ "$(id -u)" = 0 ] || abbruch "Bitte als root starten."
command -v pct >/dev/null || abbruch "Das ist kein Proxmox-Rechner (pct fehlt)."

schritt "1/5  Container „$NAME“"
ID="$(pct list | awk -v n="$NAME" 'NR>1 && $NF==n {print $1; exit}')"
if [ -n "$ID" ]; then
  ok "gibt es schon (Nummer $ID), wird weiterverwendet"
else
  FREI=$(free -m | awk '/^Mem:/{print $7}')
  if [ "$FREI" -lt $((RAM + 512)) ]; then
    hinweis "Nur $FREI MB Arbeitsspeicher frei, der Container bekommt $RAM MB. Das wird knapp."
    read -r -p "  Trotzdem weitermachen? [j/N] " a; [ "${a:-n}" = "j" ] || exit 1
  fi
  pvesm status | awk 'NR>1{print $1}' | grep -qx "$SPEICHER" || abbruch "Speicher „$SPEICHER“ gibt es nicht."
  ID="$(pvesh get /cluster/nextid)"
  echo "  Debian-12-Vorlage holen …"
  pveam update >/dev/null 2>&1
  VORLAGE="$(pveam available --section system | awk '{print $2}' | grep -E '^debian-12-standard_.*_amd64' | sort -V | tail -1)"
  [ -n "$VORLAGE" ] || abbruch "Keine Debian-12-Vorlage gefunden (Internet?)."
  pveam list local | grep -q "$VORLAGE" || pveam download local "$VORLAGE" >/dev/null || abbruch "Vorlage ließ sich nicht laden."
  # Gleiche Netzwerkbrücke wie ioBroker
  BRUECKE="$(pct config 100 2>/dev/null | sed -n 's/^net0:.*bridge=\([^,]*\).*/\1/p')"
  BRUECKE="${BRUECKE:-vmbr0}"
  pct create "$ID" "local:vztmpl/$VORLAGE" \
    --hostname "$NAME" --description "LST Baustellen-App" \
    --cores "$KERNE" --memory "$RAM" --swap "$SWAP" \
    --rootfs "$SPEICHER:$PLATTE" \
    --net0 "name=eth0,bridge=$BRUECKE,ip=dhcp" \
    --unprivileged 1 --features nesting=1 --onboot 1 --timezone host \
    >/dev/null || abbruch "Container ließ sich nicht anlegen."
  # Tailscale braucht im Container das Gerät /dev/net/tun
  cat >> "/etc/pve/lxc/$ID.conf" <<EOF
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF
  ok "angelegt: Nummer $ID, $KERNE Kerne, $RAM MB, $PLATTE GB, startet mit dem Rechner"
fi

schritt "2/5  Container starten"
pct status "$ID" | grep -q running || pct start "$ID" || abbruch "Container startet nicht."
for _ in $(seq 1 30); do
  pct exec "$ID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done
pct exec "$ID" -- getent hosts deb.debian.org >/dev/null 2>&1 || abbruch "Container hat kein Internet."
IP="$(pct exec "$ID" -- hostname -I 2>/dev/null | awk '{print $1}')"
ok "läuft, Adresse im Büronetz: ${IP:-unbekannt}"

schritt "3/5  Programm holen"
pct exec "$ID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq git ca-certificates curl >/dev/null" \
  || abbruch "Grundpakete ließen sich nicht installieren."
pct exec "$ID" -- bash -c "
  if [ -d /opt/lst-baustelle/.git ]; then cd /opt/lst-baustelle && git fetch -q origin && git checkout -q '$ZWEIG' && git pull -q --ff-only;
  else git clone -q -b '$ZWEIG' '$REPO' /opt/lst-baustelle; fi" || abbruch "Programm ließ sich nicht von GitHub holen."
ok "$(pct exec "$ID" -- git -C /opt/lst-baustelle log -1 --format='Stand %h vom %cd' --date=format:'%d.%m.%Y %H:%M')"

schritt "4/5  App im Container einrichten"
pct exec "$ID" -- bash /opt/lst-baustelle/linux/container-einrichten.sh || abbruch "Einrichtung im Container ging nicht."

schritt "5/5  Nächtliche Sicherung des Containers"
if pvesm status --content backup 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$SICHERUNG"; then
  if pvesh get /cluster/backup --output-format json 2>/dev/null | grep -q '"id":"lst-baustelle"'; then
    ok "Sicherungsauftrag gibt es schon"
  else
    pvesh create /cluster/backup --id lst-baustelle --schedule "03:00" --storage "$SICHERUNG" \
      --vmid "$ID" --mode snapshot --compress zstd --enabled 1 \
      --prune-backups keep-daily=7,keep-weekly=4,keep-monthly=6 \
      --comment "LST Baustellen-App" >/dev/null \
      && ok "jede Nacht 3:00 Uhr nach „$SICHERUNG“ (7 Tage, 4 Wochen, 6 Monate)" \
      || hinweis "Sicherungsauftrag ließ sich nicht anlegen. Bitte in Proxmox unter Rechenzentrum › Backup anlegen."
  fi
else
  hinweis "Speicher „$SICHERUNG“ nimmt keine Sicherungen an. Bitte in Proxmox unter Rechenzentrum › Backup"
  hinweis "einen Auftrag für Container $ID anlegen (oder unter Speicher bei „$SICHERUNG“ den Inhalt „Backup“ erlauben)."
fi

echo
echo "Fertig. Container „$NAME“ hat die Nummer $ID."
echo "  Aktualisieren:   pct exec $ID -- bash /opt/lst-baustelle/linux/aktualisieren.sh"
echo "  Hineinschauen:   pct enter $ID"
