"""Nächtliche Sicherung der App-Daten.

- Datenbank: konsistente Kopie über die SQLite-Sicherungsfunktion (auch während der Server läuft),
  aufbewahrt 60 Tage in <Daten>/sicherung/.
- Optional Spiegel nach iCloud Drive oder auf eine zweite Platte: Umgebungsvariable
  LST_SPIEGEL=<Ordner>. Gespiegelt werden Datenbank-Sicherungen, Dokumente und Originalfotos.
  Gelöscht wird im Spiegel nie etwas.

Aufruf: python3 -m lst.sicherung   (der Autostart erledigt das jede Nacht um 2:30 Uhr)
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

from . import speicher

BEHALTEN_TAGE = 60


def datenbank_sichern() -> Path:
    ziel = speicher.pfad("sicherung", f"lst-{date.today():%y%m%d}.db")
    quelle = sqlite3.connect(speicher.DATEN / "lst.db")
    kopie = sqlite3.connect(ziel)
    with kopie:
        quelle.backup(kopie)
    quelle.close()
    kopie.close()
    grenze = time.time() - BEHALTEN_TAGE * 86400
    for alt in (speicher.DATEN / "sicherung").glob("lst-*.db"):
        if alt.stat().st_mtime < grenze:
            alt.unlink()
    return ziel


def spiegeln(ziel: Path) -> int:
    n = 0
    for teil in ("sicherung", "dokumente", "fotos"):
        q = speicher.DATEN / teil
        if not q.exists():
            continue
        for datei in q.rglob("*"):
            if datei.is_dir() or datei.name.endswith("_v.jpg"):
                continue
            z = ziel / teil / datei.relative_to(q)
            if z.exists() and z.stat().st_size == datei.stat().st_size and z.stat().st_mtime >= datei.stat().st_mtime:
                continue
            z.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(datei, z)
            n += 1
    return n


def main():
    speicher.einrichten()
    db = datenbank_sichern()
    print("Datenbank gesichert:", db)
    spiegel = os.environ.get("LST_SPIEGEL")
    if spiegel:
        p = Path(os.path.expanduser(spiegel))
        if p.parent.exists():
            print("Gespiegelt nach", p, ":", spiegeln(p), "Dateien neu")
        else:
            print("Spiegel-Ordner nicht erreichbar:", p)
    speicher.protokoll("sicherung", "system", datei=db.name, spiegel=bool(spiegel))


if __name__ == "__main__":
    main()
