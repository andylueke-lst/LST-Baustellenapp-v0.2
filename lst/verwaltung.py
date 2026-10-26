"""Kleine Befehle für die Einrichtung auf dem Mac mini.

  python3 -m lst.verwaltung pin Andy        PIN für Andy setzen (wird abgefragt, nicht angezeigt)
  python3 -m lst.verwaltung liste           Mitarbeiter mit Rolle und PIN-Status
  python3 -m lst.verwaltung adresse         Adresse für das iPad anzeigen (über Tailscale)
  python3 -m lst.verwaltung notion          Notion-Schlüssel eintragen (verdeckte Eingabe)
  python3 -m lst.verwaltung mail            Zugang zum Postfach eintragen (verdeckte Eingabe)
"""
from __future__ import annotations

import getpass
import json
import shutil
import subprocess
import sys

from werkzeug.security import generate_password_hash

from . import speicher


def pin(name: str):
    speicher.einrichten()
    leute = speicher.stamm("mitarbeiter", [])
    m = next((x for x in leute if x["vorname"].lower() == name.lower() or x["id"] == name), None)
    if not m:
        raise SystemExit(f"Niemand mit dem Namen {name}. Vorhanden: {', '.join(x['vorname'] for x in leute)}")
    for _ in range(3):
        a = getpass.getpass(f"Neue PIN für {m['vorname']} (4–6 Ziffern): ")
        if not (a.isdigit() and 4 <= len(a) <= 6):
            print("Bitte 4 bis 6 Ziffern.")
            continue
        if getpass.getpass("PIN wiederholen: ") != a:
            print("Die beiden Eingaben sind verschieden.")
            continue
        m["pin_hash"] = generate_password_hash(a)
        speicher.stamm_setzen("mitarbeiter", leute)
        speicher.protokoll("pin_gesetzt", "einrichtung", mitarbeiter=m["id"])
        print(f"PIN für {m['vorname']} gespeichert.")
        return
    raise SystemExit("Keine PIN gesetzt.")


def liste():
    speicher.einrichten()
    for m in speicher.stamm("mitarbeiter", []):
        print(f"{m['vorname']:<10} {m.get('nachname', ''):<14} {m.get('rolle', ''):<12} "
              f"{'PIN gesetzt' if m.get('pin_hash') else 'PIN fehlt':<12} {'aktiv' if m.get('aktiv', True) else 'inaktiv'}")


def tailscale() -> str | None:
    for kandidat in ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        pfad = shutil.which(kandidat) or (kandidat if kandidat.startswith("/") else None)
        if pfad:
            try:
                subprocess.run([pfad, "version"], capture_output=True, check=True, timeout=10)
                return pfad
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def adresse():
    ts = tailscale()
    if not ts:
        print("Tailscale nicht gefunden. Im Büro-WLAN: http://<Name des Mac mini>.local:8080")
        return
    try:
        st = json.loads(subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=10).stdout)
        name = (st.get("Self") or {}).get("DNSName", "").rstrip(".")
    except (OSError, ValueError, subprocess.SubprocessError):
        name = ""
    print(f"https://{name}/app/" if name else "Tailscale läuft, aber der Gerätename ist nicht lesbar (angemeldet?).")


def _geheim_setzen(teil: str, werte: dict):
    g = speicher.geheim()
    g[teil] = {**(g.get(teil) or {}), **werte}
    p = speicher.pfad("geheim.json")
    p.write_text(json.dumps(g, indent=2, ensure_ascii=False))
    p.chmod(0o600)


def notion_eintragen():
    speicher.einrichten()
    token = getpass.getpass("Notion-Schlüssel der Integration (beginnt mit ntn_ oder secret_): ").strip()
    if not token.startswith(("ntn_", "secret_")):
        raise SystemExit("Das sieht nicht wie ein Notion-Schlüssel aus. Nichts gespeichert.")
    ds = input("Datenquelle LST Projekte [644d7ac7-3929-4aea-8f1e-4d9cec143d1a]: ").strip() or "644d7ac7-3929-4aea-8f1e-4d9cec143d1a"
    _geheim_setzen("notion", {"token": token, "projekte": ds, "schreiben": False})
    from . import notion
    try:
        n = len(notion.projekte_abfragen())
        print(f"Verbindung klappt: {n} bestätigte Projekte im Zeitraum gefunden.")
    except notion.NotionFehler as e:
        print("Gespeichert, aber der Test ging nicht:", e)


def mail_eintragen():
    speicher.einrichten()
    host = input("SMTP-Server (z. B. smtp.udmedia.de): ").strip()
    port = int(input("Port [587]: ").strip() or 587)
    benutzer = input("Benutzer / Adresse (z. B. lieferschein@lueke-strahlt.de): ").strip()
    passwort = getpass.getpass("Passwort: ")
    _geheim_setzen("mail", {"host": host, "port": port, "benutzer": benutzer, "passwort": passwort,
                            "absender": f"LST Lüke-Strahl-Technik <{benutzer}>", "antwort_an": "post@lueke-strahlt.de",
                            "bcc": "post@lueke-strahlt.de", "verbindung": "ssl" if port == 465 else "starttls"})
    print("Gespeichert. Gilt sofort – im Büro im Postausgang „Jetzt senden“ probieren.")


if __name__ == "__main__":
    befehl = sys.argv[1] if len(sys.argv) > 1 else ""
    if befehl == "pin" and len(sys.argv) > 2:
        pin(sys.argv[2])
    elif befehl == "liste":
        liste()
    elif befehl == "adresse":
        adresse()
    elif befehl == "notion":
        notion_eintragen()
    elif befehl == "mail":
        mail_eintragen()
    else:
        print(__doc__)
