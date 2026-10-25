"""Tagesbericht mit Kundenunterschrift (optional pro Einsatz)."""
from __future__ import annotations

import io

from reportlab.lib.utils import ImageReader

from . import basis
from .basis import GRAU, RAND_L, TEXT, Seite


def tagesbericht(daten: dict) -> bytes:
    """daten: nummer, tag_nr, datum, einsatzort, kunde, ls, text, bes, mitarbeiter[],
    strahlmittel, einstellungen, fotos_anzahl, unterschrift{name, zeit, bild}"""
    buf = io.BytesIO()
    s = Seite(buf, oben_erst=190)
    s.text(f"Tagesbericht Tag {daten['tag_nr']} – {basis.datum(daten['datum'])}", 11, fett=True)
    s.abstand(8)
    s.feld("Einsatzort", daten["einsatzort"])
    if daten.get("kunde") and daten["kunde"] != daten["einsatzort"]:
        s.feld("Kunde", daten["kunde"])
    s.feld("Lieferschein", daten.get("ls") or "folgt")
    s.abstand(10)
    s.text("Arbeitsbericht:", 9)
    s.text(daten.get("text") or "–", 9)
    if daten.get("bes"):
        s.text(daten["bes"], 9)
    s.abstand(8)
    s.feld("Mitarbeiter", ", ".join(daten.get("mitarbeiter") or []) or "–")
    s.feld("Strahlmittel", daten.get("strahlmittel") or "–")
    s.feld("Einstellungen", daten.get("einstellungen") or "–")
    s.feld("Fotos des Tages", str(daten.get("fotos_anzahl", 0)))
    s.abstand(14)
    s.text("Die Arbeiten an diesem Tag wurden wie beschrieben ausgeführt.", 9)
    u = daten.get("unterschrift") or {}
    s.abstand(6)
    s.feld("Name:", u.get("name", ""))
    s.feld("Datum:", u.get("zeit", ""))
    s.platz(70)
    s.c.setFont(s.f["normal"], 9)
    s.c.setFillColorRGB(*TEXT)
    s.c.drawString(RAND_L, s.y - 50, "Unterschrift:")
    if u.get("bild"):
        s.c.drawImage(ImageReader(io.BytesIO(u["bild"])), RAND_L + 110, s.y - 56, width=200, height=54,
                      preserveAspectRatio=True, anchor="sw", mask="auto")
    s.c.setStrokeColorRGB(*TEXT)
    s.c.setLineWidth(0.5)
    s.c.line(RAND_L + 110, s.y - 58, RAND_L + 330, s.y - 58)
    s.fertig()
    return basis.mit_briefpapier(buf.getvalue(), links_unten=f"Tagesbericht {daten['nummer']}")
