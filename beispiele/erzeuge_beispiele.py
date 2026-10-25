"""Erzeugt Beispiel-Dokumente zum Ansehen.

Aufruf:  python3 beispiele/erzeuge_beispiele.py <lexware-lieferschein.pdf> <ordner-mit-fotos>

Die Fotos werden der Reihe nach auf vier Beispiel-Anlagen verteilt.
Ergebnis landet in ausgabe/.
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lst.dokumente import abschluss, basis, tagesbericht  # noqa: E402

AUSGABE = Path(__file__).resolve().parents[1] / "ausgabe"


def unterschrift_png() -> bytes:
    im = Image.new("RGBA", (600, 160), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    pts = [(30 + x * 5.2, 95 + 32 * math.sin(x / 7) * math.exp(-x / 160) - x * 0.12) for x in range(100)]
    d.line(pts, fill=(26, 42, 108, 255), width=5)
    d.line([(330, 60), (350, 128), (372, 70), (395, 125), (420, 75), (520, 92)], fill=(26, 42, 108, 255), width=5)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def beispiel_einsatz(fotos: list[Path]) -> tuple[dict, dict]:
    anl = [("a1", "Kühlturm 1"), ("a2", "Kühlturm 2"), ("a3", "V-Tauscher 1"), ("a4", "V-Tauscher 2")]
    verteilung = {"a1": ([0, 1], [6, 7]), "a2": ([2], [8]), "a3": ([3, 4], [9, 10]), "a4": ([5], [11])}
    foto_liste = []
    for aid, (v, n) in verteilung.items():
        for i in v:
            if i < len(fotos):
                foto_liste.append({"id": f"f{i}", "anlage": aid, "phase": "vorher"})
        for i in n:
            if i < len(fotos):
                foto_liste.append({"id": f"f{i}", "anlage": aid, "phase": "nachher"})
    z = lambda p, start="buero", ende="hotel", pause=45: {"start": start, "ende": ende, "p": p, "pause": pause}
    tage = []
    texte = ["Anreise, Aufbau, Wasserwannen Kühlturm 1 gereinigt.",
             "Wasserwannen Kühlturm 2 und Einlaufgitter außen gereinigt.",
             "V-Tauscher 1 und 2 auf dem Dach gestrahlt.",
             "Tischtauscher 1 bis 6 gestrahlt.",
             "Tischtauscher 7 bis 11 und Lamellenrückkühler gestrahlt, Abbau."]
    for i, t in enumerate(texte):
        datum = f"2026-10-{12 + i:02d}"
        p = {"2": "07:15", "3": "07:45", "4": "16:30", "5": "16:45"} if i else \
            {"1": "06:00", "2": "06:30", "3": "10:30", "4": "17:00", "5": "17:15"}
        tage.append({"datum": datum, "fertig": True, "unterschrift": "ja" if i == 1 else None,
                     "zeiten": {"m1": z(p, "buero" if i == 0 else "hotel"), "m2": z(p, "buero" if i == 0 else "hotel")},
                     "bericht": {"text": t, "sm": "ultrafein", "grund": True, "kg": None,
                                 "einst": {"s1": {"air": "5", "media": "3", "duese": "schmal"}},
                                 "bes": "Ein Tischtauscher stark verölt, Nachreinigung empfohlen." if i == 3 else ""}})
    e = {"einsatzort": "Zimmermann Lauenau", "kunde": "Zimmermann GmbH", "adresse": "Carl-Sasse-Straße 1, 31867 Lauenau",
         "ls": None, "reinigungsart": "Reinigung Wärmetauscher und Kühltürme", "von": "2026-10-12", "bis": "2026-10-16",
         "team": {"vor": "m1", "fach": "m2"}, "geraete": {"strahl": ["s1"]},
         "anlagen": [{"id": a, "name": n} for a, n in anl], "fotos": foto_liste, "tage": tage}
    stamm = {"mitarbeiter": {"m1": "Matthias", "m2": "Faro"},
             "strahlgeraete": {"s1": {"name": "Loki", "typ": "loki"}},
             "typen": {"loki": {"felder": [
                 {"key": "air", "label": "Air", "werte": [str(x) for x in range(1, 8)]},
                 {"key": "media", "label": "Media", "werte": [str(x) for x in range(1, 8)]},
                 {"key": "duese", "label": "Düse", "werte": ["breit", "schmal", "rund"]}]}}}
    return e, stamm


def main():
    lexware = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    ordner = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    fotos = sorted(ordner.glob("*.jp*g")) if ordner else []
    AUSGABE.mkdir(exist_ok=True)
    e, stamm = beispiel_einsatz(fotos)
    quelle = lambda fid: fotos[int(fid[1:])]
    sig = {"name": "Rebmann", "zeit": "16.10.2026, 14:30 Uhr", "bild": unterschrift_png(), "eingesehen": True,
           "zeiten_auf_ls": False}
    docs = abschluss.einsatz_dokumente(e, stamm, quelle, lexware, sig, heute="2026-10-16")
    for key, (name, pdf, info) in docs.items():
        (AUSGABE / name).write_bytes(pdf)
        print(f"{key:12s} {len(pdf) / 1048576:5.2f} MB  {name}  {info}")

    e["ls"] = docs["lieferschein"][0].split()[-1].replace(".pdf", "")
    auswahl = [f["id"] for f in e["fotos"]][:8]
    name, pdf, info = abschluss.fotobericht_aus_auswahl(
        e, auswahl, quelle, "Sehr geehrte Damen und Herren, anbei der Fotobericht zur Reinigung in Lauenau.",
        heute="2026-10-19")
    (AUSGABE / name).write_bytes(pdf)
    print(f"{'fotobericht':12s} {len(pdf) / 1048576:5.2f} MB  {name}  {info}")

    t = e["tage"][1]
    tb = tagesbericht.tagesbericht({
        "nummer": "TB" + basis.nummer_ziffern(e["ls"]) + "-2", "tag_nr": 2, "datum": t["datum"],
        "einsatzort": e["einsatzort"], "kunde": e["kunde"], "ls": e["ls"], "text": t["bericht"]["text"],
        "mitarbeiter": ["Matthias", "Faro"], "strahlmittel": "CaCO3 ultrafein (Grundmenge)",
        "einstellungen": "Loki: Air 5, Media 3, Düse schmal", "fotos_anzahl": 3,
        "unterschrift": {"name": "Rebmann", "zeit": "13.10.2026, 16:25 Uhr", "bild": unterschrift_png()}})
    name = basis.dateiname("2026-10-13", e["kunde"], e["einsatzort"], "Tagesbericht",
                           "TB" + basis.nummer_ziffern(e["ls"]) + "-2")
    (AUSGABE / name).write_bytes(tb)
    print(f"{'tagesbericht':12s} {len(tb) / 1048576:5.2f} MB  {name}")


if __name__ == "__main__":
    main()
