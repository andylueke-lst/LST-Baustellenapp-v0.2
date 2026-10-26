"""Legt Testdaten an und spielt einen Job-Abschluss über die Schnittstelle durch.

Aufruf:  python3 -m lst.demo <lexware-lieferschein.pdf> <ordner-mit-fotos>
Danach:  python3 -m lst.server  und im Browser http://localhost:8080 öffnen.
Anmeldung im Test: Andy oder Tina (Büro), PIN 1234.
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

from . import speicher

TEST_PIN = "1234"


def stammdaten():
    h = generate_password_hash(TEST_PIN)
    speicher.stamm_setzen("mitarbeiter", [
        {"id": "m4", "vorname": "Andy", "nachname": "Lüke", "rolle": "admin", "pin_hash": h, "aktiv": True},
        {"id": "m5", "vorname": "Tina", "nachname": "", "rolle": "admin", "pin_hash": h, "aktiv": True},
        {"id": "m1", "vorname": "Matthias", "nachname": "", "rolle": "technik", "pin_hash": h, "aktiv": True},
        {"id": "m2", "vorname": "Faro", "nachname": "", "rolle": "mitarbeiter", "pin_hash": h, "aktiv": True},
        {"id": "m3", "vorname": "Arthur", "nachname": "", "rolle": "mitarbeiter", "pin_hash": h, "aktiv": True},
    ])
    speicher.stamm_setzen("strahlgeraete", [{"id": "s1", "name": "Loki", "typ": "loki"}])
    speicher.stamm_setzen("geraetetypen", {"loki": {"name": "Loki", "felder": [
        {"key": "air", "label": "Air", "werte": [str(x) for x in range(1, 8)]},
        {"key": "media", "label": "Media", "werte": [str(x) for x in range(1, 8)]},
        {"key": "duese", "label": "Düse", "werte": ["breit", "schmal", "rund"]}]}})


def main():
    from .server import app
    sys.path.insert(0, str(speicher.ROOT))
    from beispiele.erzeuge_beispiele import beispiel_einsatz, unterschrift_png

    lexware = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else None
    fotos = sorted(Path(sys.argv[2]).glob("*.jp*g")) if len(sys.argv) > 2 else []
    speicher.einrichten()
    stammdaten()

    e, _ = beispiel_einsatz(fotos)
    e.update({"id": "demo-zimmermann", "status": "inarbeit",
              "apKunde": {"name": "Frau Manske", "mail": "auftraggeber@example.com"},
              "apOrt": {"name": "Herr Rebmann", "mail": "vorort@example.com"}})
    foto_plan = e.pop("fotos")

    c = app.test_client()
    r = c.post("/login", data={"wer": "m1", "pin": TEST_PIN})
    assert r.status_code == 302, "Anmeldung fehlgeschlagen"
    assert c.post("/api/einsatz", json=e).json["ok"]
    for f in foto_plan:
        datei = fotos[int(f["id"][1:])]
        r = c.post(f"/api/einsatz/{e['id']}/foto", data={
            "foto": (io.BytesIO(datei.read_bytes()), datei.name), "anlage": f["anlage"], "phase": f["phase"], "id": f["id"]},
            content_type="multipart/form-data")
        assert r.json["ok"], r.json
    if lexware:
        speicher.pfad("dokumente", e["id"], "lexware.pdf").write_bytes(lexware)
        e["ls"] = "LS260067"
        speicher.einsatz_speichern(e)
    sig = {"name": "Rebmann", "bild": "data:image/png;base64," + base64.b64encode(unterschrift_png()).decode(),
           "eingesehen": True, "zeiten_auf_ls": False, "datum": "2026-10-16", "versand": True, "mail": "vorort@example.com"}
    r = c.post(f"/api/einsatz/{e['id']}/abschluss", json=sig)
    print(json.dumps(r.json, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
