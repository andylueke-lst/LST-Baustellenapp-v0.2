"""Tankliste als PDF: Übersicht je Gerät/Fahrzeug, danach alle Einträge je Gerät."""
from __future__ import annotations

import io

from reportlab.pdfbase import pdfmetrics

from . import basis
from .basis import GRAU, RAND_L, TEXT, Seite

ZEILE = 13.5


def _z(x, stellen=1):
    from ..auswertung import zahl
    return zahl(x, stellen) if x not in (None, "") else "–"


class Tabelle:
    """Einfache Tabelle mit Kopfzeile, die bei Seitenwechsel wiederholt wird.
    spalten: [(Überschrift, x-Versatz, rechtsbündig)]"""

    def __init__(self, s: Seite, spalten, titel_fortsetzung=""):
        self.s, self.sp, self.titel = s, spalten, titel_fortsetzung

    def zelle(self, text, nr, fett=False, farbe=TEXT, groesse=8.5):
        s = self.s
        _, x, rechts = self.sp[nr]
        f = s.f["fett" if fett else "normal"]
        text = str(text)
        if not rechts:
            naechste = self.sp[nr + 1][1] - 6 if nr + 1 < len(self.sp) else s.breite
            if self.sp[nr + 1:] and self.sp[nr + 1][2]:
                naechste -= 34
            while pdfmetrics.stringWidth(text, f, groesse) > naechste - x and len(text) > 3:
                text = text.rstrip("…")[:-1].rstrip() + "…"
        s.c.setFont(f, groesse)
        s.c.setFillColorRGB(*farbe)
        (s.c.drawRightString if rechts else s.c.drawString)(RAND_L + x, s.y, text)

    def kopf(self):
        s = self.s
        s.platz(ZEILE * 2)
        s.y -= ZEILE
        for n, (t, _, _) in enumerate(self.sp):
            self.zelle(t, n, farbe=GRAU, groesse=8)
        s.c.setStrokeColorRGB(*GRAU)
        s.c.setLineWidth(0.4)
        s.c.line(RAND_L, s.y - 4, RAND_L + s.breite, s.y - 4)
        s.y -= 4

    def zeile(self, werte, fett=False):
        s = self.s
        if s.y - ZEILE < s.unten:
            s.neue_seite()
            if self.titel:
                s.text(self.titel + " (Fortsetzung)", 9, fett=True)
            self.kopf()
        s.y -= ZEILE
        for n, w in enumerate(werte):
            self.zelle(w, n, fett=fett)


def tankliste(gruppen: list[dict], von: str, bis: str) -> bytes:
    """gruppen: aus auswertung.tank_summen()"""
    buf = io.BytesIO()
    zr = basis.zeitraum(von, bis)
    s = Seite(buf, oben_erst=190, titel_folge=f"Tanken und Öl {zr}")
    s.text("Tanken und Öl", 11, fett=True)
    s.abstand(6)
    s.feld("Zeitraum", zr)
    s.abstand(4)
    if not gruppen:
        s.text("Keine Einträge in diesem Zeitraum.", 9)
    else:
        t = Tabelle(s, [("Gerät / Fahrzeug", 0, False), ("Tankv.", 200, True), ("Diesel", 252, True),
                        ("Betrag", 310, True), ("Verbrauch", 386, True), ("Öl nachgef.", 453, True)])
        t.kopf()
        for x in gruppen:
            v = f"{_z(x.get('verbrauch'))} {'l/100km' if x['art'] == 'km' else 'l/h'}" if x.get("verbrauch") else "–"
            t.zeile([x["geraet"], str(x["anzahl"]), f"{_z(x['liter'])} l", f"{_z(x['betrag'], 2)} €", v,
                     f"{_z(x['oel_l'])} l"])
        s.abstand(6)
        s.text("Verbrauch: getankte Liter geteilt durch Kilometer bzw. Betriebsstunden seit dem vorigen Tanken "
               "desselben Geräts (Volltank-Methode).", 7.5, farbe=GRAU)
        for x in gruppen:
            km = x["art"] == "km"
            s.abstand(14)
            s.platz(ZEILE * 4)
            s.text(x["geraet"], 10, fett=True)
            t = Tabelle(s, [("Datum", 0, False), ("km-Stand" if km else "Betr.-Std.", 112, True),
                            ("Diesel", 160, True), ("Betrag", 214, True), ("€/l", 252, True),
                            ("Verbrauch", 318, True), ("Öl", 330, False), ("Wer", 400, False)],
                        titel_fortsetzung=x["geraet"])
            t.kopf()
            for e in x["eintraege"]:
                d = basis.d(e["datum"])
                oel = f"{_z(e['oel_l'])} l nachgef." if e.get("oel_l") else ("geprüft" if e.get("oel_geprueft") else "")
                t.zeile([f"{basis.WOCHENTAGE[d.weekday()]} {d:%d.%m.%Y}", _z(e["stand"]),
                         f"{_z(e['liter'])} l" if e.get("liter") else "–",
                         f"{_z(e['betrag'], 2)} €" if e.get("betrag") is not None else "–",
                         _z(e.get("preis"), 3) if e.get("preis") else "–",
                         _z(e.get("verbrauch")) if e.get("verbrauch") is not None else "–", oel, e.get("wer") or ""])
            t.zeile(["Summe", "", f"{_z(x['liter'])} l", f"{_z(x['betrag'], 2)} €", "",
                     _z(x.get("verbrauch")) if x.get("verbrauch") else "", f"{_z(x['oel_l'])} l Öl" if x["oel_l"] else "", ""],
                    fett=True)
            s.text(f"Verbrauch in {'l/100 km' if km else 'l je Betriebsstunde'}.", 7.5, farbe=GRAU)
    s.fertig()
    return basis.mit_briefpapier(buf.getvalue(), links_unten=f"Tanken und Öl {zr}")
