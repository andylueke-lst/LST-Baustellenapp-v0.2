"""Arbeitsbericht für interne Arbeiten (ohne Kunde, ohne Unterschrift)."""
from __future__ import annotations

import io

from . import basis
from .basis import GRAU, RAND_L, Seite


def dauer_text(von: str | None, bis: str | None) -> str:
    if not von or not bis:
        return ""
    h1, m1 = map(int, von.split(":"))
    h2, m2 = map(int, bis.split(":"))
    m = (h2 * 60 + m2) - (h1 * 60 + m1)
    if m < 0:
        m += 1440
    return f"{m // 60}:{m % 60:02d} h"


def arbeitsbericht(daten: dict) -> bytes:
    """daten: titel, projekt, beschreibung, name, datum, von, bis, text, material,
    fotos: [{phase, quelle}] (Pfad oder Bytes)"""
    buf = io.BytesIO()
    s = Seite(buf, oben_erst=190, titel_folge=f"Arbeitsbericht intern – {daten['titel']}")
    s.text("Arbeitsbericht intern", 11, fett=True)
    s.abstand(8)
    s.feld("Aufgabe", daten["titel"])
    if daten.get("projekt"):
        s.feld("Projekt", daten["projekt"])
    if daten.get("zeiten"):     # gemeinsame Aufgabe: eine Zeile je Person
        s.feld("Erledigt von", ", ".join(z["name"] for z in daten["zeiten"]))
        for z in daten["zeiten"]:
            dauer = dauer_text(z.get("von"), z.get("bis"))
            s.feld(z["name"], (basis.datum(z["datum"]) + ", " if z.get("datum") else "")
                   + f"{z.get('von') or '?'}–{z.get('bis') or '?'} Uhr" + (f" ({dauer})" if dauer else ""))
    else:
        s.feld("Erledigt von", daten.get("name") or "–")
        s.feld("Datum", basis.datum(daten["datum"]) if daten.get("datum") else "–")
    if not daten.get("zeiten") and (daten.get("von") or daten.get("bis")):
        dauer = dauer_text(daten.get("von"), daten.get("bis"))
        s.feld("Zeit", f"{daten.get('von') or '?'}–{daten.get('bis') or '?'} Uhr" + (f" ({dauer})" if dauer else ""))
    s.abstand(10)
    s.text("Was wurde gemacht:", 9, fett=True)
    s.text(daten.get("text") or "–", 9)
    if daten.get("material"):
        s.abstand(8)
        s.text("Material:", 9, fett=True)
        s.text(daten["material"], 9)
    if daten.get("beschreibung"):
        s.abstand(8)
        s.text("Aufgabe laut Notion:", 8, fett=True, farbe=GRAU)
        s.text(daten["beschreibung"], 8, farbe=GRAU)

    for phase, ueberschrift in (("vorher", "Fotos vorher"), ("nachher", "Fotos nachher")):
        bilder = [f["quelle"] for f in daten.get("fotos") or [] if f.get("phase") == phase]
        if not bilder:
            continue
        s.abstand(12)
        s.text(ueberschrift, 9, fett=True)
        s.abstand(4)
        w = (s.breite - 12) / 2
        h = w * 0.75
        for k in range(0, len(bilder), 2):
            s.platz(h + 8)
            oben = s.y
            for n, q in enumerate(bilder[k:k + 2]):
                jpeg, bw, bh = basis.foto_jpeg(q, 1400, 82)
                s.bild(jpeg, RAND_L + n * (w + 12), oben, w, h, bw, bh)
            s.y = oben - h - 8
    s.fertig()
    return basis.mit_briefpapier(buf.getvalue(), links_unten="Arbeitsbericht intern")
