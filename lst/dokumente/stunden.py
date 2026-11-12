"""Stundennachweis als PDF (für den Steuerberater): je Mitarbeiter eine eigene Seite."""
from __future__ import annotations

import io

from reportlab.pdfbase import pdfmetrics

from . import basis
from .basis import GRAU, RAND_L, TEXT, Seite

# Spalten: (Überschrift, x-Versatz, rechtsbündig)
SPALTEN = [("Datum", 0, False), ("Tätigkeit", 62, False), ("Beginn", 268, True), ("Ende", 306, True),
           ("Pause", 344, True), ("Fahrt", 382, True), ("Stunden", 453, True)]
ZEILE = 13.5


def _hm(m):
    if m is None:
        return "–"
    return f"{m // 60}:{m % 60:02d}"


def _zelle(s: Seite, text: str, nr: int, fett=False, farbe=TEXT, groesse=8.5):
    _, x, rechts = SPALTEN[nr]
    f = s.f["fett" if fett else "normal"]
    s.c.setFont(f, groesse)
    s.c.setFillColorRGB(*farbe)
    if rechts:
        s.c.drawRightString(RAND_L + x, s.y, text)
    else:
        # Tätigkeit kürzen, damit sie nicht in „Beginn“ läuft
        if nr == 1:
            max_b = SPALTEN[2][1] - SPALTEN[1][1] - 36
            while pdfmetrics.stringWidth(text, f, groesse) > max_b and len(text) > 4:
                text = text.rstrip("…")[:-1].rstrip() + "…"
        s.c.drawString(RAND_L + x, s.y, text)


def _kopf(s: Seite):
    s.platz(ZEILE * 2)
    s.y -= ZEILE
    for n, (t, _, _) in enumerate(SPALTEN):
        _zelle(s, t, n, farbe=GRAU, groesse=8)
    s.c.setStrokeColorRGB(*GRAU)
    s.c.setLineWidth(0.4)
    s.c.line(RAND_L, s.y - 4, RAND_L + s.breite, s.y - 4)
    s.y -= 4


def stundennachweis(gruppen: list[dict], von: str, bis: str) -> bytes:
    """gruppen: aus auswertung.nach_mitarbeiter()"""
    buf = io.BytesIO()
    s = Seite(buf, oben_erst=190, titel_folge=f"Stundennachweis {basis.zeitraum(von, bis)}")
    for k, g in enumerate(gruppen):
        if k:
            s.neue_seite()
        s.text("Stundennachweis", 11, fett=True)
        s.abstand(6)
        s.feld("Mitarbeiter", g["name"])
        s.feld("Zeitraum", basis.zeitraum(von, bis))
        sm = g["summe"]
        s.feld("Arbeitstage", str(sm["tage"]))
        s.feld("Stunden gesamt", f"{_hm(sm['gesamt'])} h  (" + f"{sm['gesamt'] / 60:.2f}".replace(".", ",") + " Std.)")
        s.abstand(6)
        _kopf(s)
        offen = 0
        for z in g["zeilen"]:
            if s.y - ZEILE < s.unten:
                s.neue_seite()
                s.text(g["name"] + " (Fortsetzung)", 9, fett=True)
                _kopf(s)
            s.y -= ZEILE
            d = basis.d(z["datum"])
            if z["art"] in ("Einsatz", "Händisch"):
                taet = z["wo"] or ("Einsatz" if z["art"] == "Einsatz" else "Arbeitszeit")
            else:
                taet = z["art"] + (" · " + z["wo"] if z["wo"] else "")
            _zelle(s, f"{basis.WOCHENTAGE[d.weekday()]} {d:%d.%m.}", 0)
            _zelle(s, taet, 1)
            _zelle(s, z.get("beginn") or "–", 2)
            _zelle(s, z.get("ende") or "–", 3)
            _zelle(s, _hm(z["pause"]) if z["pause"] else "–", 4)
            _zelle(s, _hm(z["fahrt"]) if z["fahrt"] else "–", 5)
            unvollst = z["gesamt"] is None or bool(z["fehlt"])
            offen += unvollst
            _zelle(s, _hm(z["gesamt"]) + (" *" if unvollst else ""), 6, fett=True)
        s.c.setStrokeColorRGB(*GRAU)
        s.c.line(RAND_L, s.y - 5, RAND_L + s.breite, s.y - 5)
        s.platz(ZEILE + 6)
        s.y -= ZEILE + 6
        _zelle(s, "Summe", 1, fett=True)
        _zelle(s, _hm(sm["fahrt"] + sm["reise"]) if (sm["fahrt"] + sm["reise"]) else "–", 5, fett=True)
        _zelle(s, _hm(sm["gesamt"]) + " h", 6, fett=True, groesse=9)
        s.abstand(10)
        s.text("Stunden = Arbeitszeit vom Arbeitsbeginn bis Feierabend abzüglich Pause, einschließlich Fahrt- und "
               "Reisezeiten.", 7.5, farbe=GRAU)
        if offen:
            s.text(f"* {offen} Eintrag/Einträge unvollständig (Zeit fehlt) und nur teilweise oder nicht mitgezählt.",
                   7.5, farbe=GRAU)
    if not gruppen:
        s.text("Stundennachweis", 11, fett=True)
        s.abstand(6)
        s.text(f"Keine Zeiten im Zeitraum {basis.zeitraum(von, bis)}.", 9)
    s.fertig()
    return basis.mit_briefpapier(buf.getvalue(), links_unten=f"Stundennachweis {basis.zeitraum(von, bis)}")
