"""Lieferschein: Lexware-Seiten unverändert übernehmen, Arbeitsnachweis als
letzte Seite aus der App anhängen, Seitenzählung auf allen Seiten angleichen."""
from __future__ import annotations

import io
import re

import pikepdf
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from . import basis
from .basis import GRAU, RAND_L, TEXT, Seite, config

STRAHLMITTEL = {"ultrafein": "CaCO3 ultrafein", "fein": "CaCO3 fein", "sonst": "sonstiges"}


def arbeitsnachweis(daten: dict, oben: float = 188) -> bytes:
    """Erzeugt die App-Seite(n) als PDF ohne Briefpapier.

    daten: ls, einsatzort, adresse, kunde, tage[{datum, text, bes, unterschrieben}],
    vorarbeiter, facharbeiter, strahlmittel{arten, grundmenge, kg},
    geraete[{name, felder[{label, werte, wert}]}], zeiten[{datum, ankunft, abfahrt, vor_ort}] | None,
    fotodoku{nummer, anzahl, eingesehen, pruefsumme}, unterschrift{name, zeit, bild} | {ohne_kunde}
    """
    buf = io.BytesIO()
    s = Seite(buf, oben_erst=oben, oben_folge=oben,
              titel_folge=f"Lieferschein {daten.get('ls') or ''} – Arbeitsnachweis (Fortsetzung)")
    f = s.f

    s.text(f"Lieferschein {daten.get('ls') or '(folgt)'}", 9)
    s.abstand(6)
    s.text("Arbeitsnachweis", 9)
    s.abstand(8)
    s.feld("Einsatzort", f"{daten['einsatzort']}, {daten.get('adresse', '')}".strip(", "))
    if daten.get("kunde") and daten["kunde"] != daten["einsatzort"]:
        s.feld("Kunde", daten["kunde"])
    tage = daten.get("tage") or []
    if tage:
        s.feld("Zeitraum", basis.zeitraum(tage[0]["datum"], tage[-1]["datum"]))

    s.abstand(8)
    s.text("Arbeitsbericht:", 9)
    for i, t in enumerate(tage):
        kopf = f"Tag {i + 1} ({basis.datum(t['datum'])}): " if len(tage) > 1 else ""
        zusatz = " (Tagesbericht unterschrieben)" if t.get("unterschrieben") else ""
        s.text(kopf + (t.get("text") or "–") + (f" {t['bes']}" if t.get("bes") else "") + zusatz, 9)
        s.abstand(1)

    s.abstand(8)
    s.text("Mitarbeiter:", 9)
    zeilen = daten.get("mitarbeiter")
    if zeilen is None:   # ältere Form
        zeilen = [{"rolle": "Vorarbeiter", "name": daten.get("vorarbeiter") or "–", "tage": ""},
                  {"rolle": "Facharbeiter", "name": daten.get("facharbeiter") or "–", "tage": ""}]
    for z in zeilen or [{"rolle": "Vorarbeiter", "name": "–", "tage": ""}]:
        s.feld(z["rolle"] + ":", z["name"] + (f" ({z['tage']})" if z.get("tage") else ""))

    s.abstand(8)
    sm = daten.get("strahlmittel") or {}
    arten = {STRAHLMITTEL.get(a, a) for a in sm.get("arten", [])}
    s.text("Strahlmittel", 9)
    s.auswahl("Art:", list(STRAHLMITTEL.values()), arten)
    s.auswahl("Menge:", ["Grundmenge"], {"Grundmenge"} if sm.get("grundmenge", True) else set(),
              zusatz="" if sm.get("grundmenge", True) else f"Mehrmenge: {sm.get('kg') or 0} kg")

    for g in daten.get("geraete") or []:
        s.abstand(6)
        s.text(f"Einstellung {g['name']}:", 9)
        for fd in g["felder"]:
            s.auswahl(f"{fd['label']}:", [str(w) for w in fd["werte"]], {str(fd.get("wert"))})

    zeiten = daten.get("zeiten")
    if zeiten:
        s.abstand(10)
        s.text("Arbeitszeiten vor Ort:", 9)
        s.platz(14)
        s.y -= 13
        cols = [RAND_L, RAND_L + 110, RAND_L + 190, RAND_L + 270]
        s.c.setFont(f["normal"], 8)
        s.c.setFillColorRGB(*GRAU)
        for x, h in zip(cols, ["Datum", "Ankunft", "Abfahrt", "vor Ort"]):
            s.c.drawString(x, s.y, h)
        s.c.setFillColorRGB(*TEXT)
        for z in zeiten:
            s.platz(12)
            s.y -= 12
            for x, v in zip(cols, [basis.datum(z["datum"]), z.get("ankunft") or "–", z.get("abfahrt") or "–",
                                   z.get("vor_ort") or "–"]):
                s.c.drawString(x, s.y, v)

    fd = daten.get("fotodoku") or {}
    s.abstand(12)
    s.platz(30)
    s.y -= 13
    s.c.setFont(f["normal"], 9)
    s.c.setFillColorRGB(*TEXT)
    s.c.drawString(RAND_L, s.y, f"Fotodokumentation {fd.get('nummer', '')} ({fd.get('anzahl', 0)} Fotos):")
    x = RAND_L + 250
    s.kaestchen(x, s.y - 0.5, bool(fd.get("eingesehen")))
    s.c.drawString(x + 12, s.y, "vom Kunden vor Ort eingesehen")

    s.abstand(10)
    s.text("Oben genannte Arbeiten wurden ordnungsgemäß durchgeführt.", 9)

    u = daten.get("unterschrift") or {}
    s.abstand(10)
    if u.get("ohne_kunde"):
        s.text("Kunde nicht vor Ort – Unterschrift wird nachgeholt.", 9, fett=True)
        s.abstand(4)
        s.feld("Name:", "")
        s.feld("Datum:", "")
        s.abstand(30)
        s.platz(20)
        s.c.setStrokeColorRGB(*TEXT)
        s.c.setLineWidth(0.5)
        s.c.line(RAND_L + 110, s.y, RAND_L + 330, s.y)
        s.c.setFont(f["normal"], 9)
        s.c.drawString(RAND_L, s.y + 2, "Unterschrift:")
    else:
        s.feld("Name:", u.get("name", ""))
        s.feld("Datum:", u.get("zeit", ""))
        s.abstand(6)
        s.platz(70)
        s.c.setFont(f["normal"], 9)
        s.c.setFillColorRGB(*TEXT)
        s.c.drawString(RAND_L, s.y - 50, "Unterschrift:")
        if u.get("bild"):
            from reportlab.lib.utils import ImageReader
            s.c.drawImage(ImageReader(io.BytesIO(u["bild"])), RAND_L + 110, s.y - 56, width=200, height=54,
                          preserveAspectRatio=True, anchor="sw", mask="auto")
        s.c.setStrokeColorRGB(*TEXT)
        s.c.setLineWidth(0.5)
        s.c.line(RAND_L + 110, s.y - 58, RAND_L + 330, s.y - 58)
        s.y -= 64

    s.abstand(10)
    for z in config()["schlusstext"]:
        s.text(z, 9)

    if fd.get("pruefsumme"):
        s.c.setFont(f["normal"], 5.5)
        s.c.setFillColorRGB(*GRAU)
        s.c.drawString(RAND_L, 86, f"Prüfsumme Fotodokumentation {fd.get('nummer', '')} (SHA-256): {fd['pruefsumme']}")
    s.fertig()
    return buf.getvalue()


def _hat_arbeitsnachweis(seite_text: str) -> bool:
    return config()["lexware"]["arbeitsnachweis_erkennung"].lower() in seite_text.lower()


def zusammenfuegen(lexware_pdf: bytes | None, daten: dict) -> tuple[bytes, dict]:
    """Ergebnis: vollständiger Lieferschein + Infos (Seitenzahl, ersetzte Seite)."""
    cfg = config()
    lx = cfg["lexware"]
    info = {"lexware_seiten": 0, "arbeitsnachweis_ersetzt": False}

    if not lexware_pdf:
        pdf = basis.mit_briefpapier(arbeitsnachweis(daten))
        info["seiten"] = len(pikepdf.open(io.BytesIO(pdf)).pages)
        return pdf, info

    texte = [p.extract_text() or "" for p in PdfReader(io.BytesIO(lexware_pdf)).pages]
    ziel = pikepdf.open(io.BytesIO(lexware_pdf))
    if len(ziel.pages) > 1 and _hat_arbeitsnachweis(texte[-1]):
        del ziel.pages[-1]
        info["arbeitsnachweis_ersetzt"] = True
    n_lex = len(ziel.pages)
    info["lexware_seiten"] = n_lex

    # App-Seite(n) auf dem Briefpapier mit Spruch, wie die Lexware-Seiten, Fußzeile identisch.
    app = pikepdf.open(io.BytesIO(basis.mit_briefpapier(arbeitsnachweis(daten), immer_erstseite=True, wie_lexware=True)))
    for seite in app.pages:
        ziel.pages.append(seite)
    gesamt = len(ziel.pages)
    info["seiten"] = gesamt

    # Seitenzahlen neu: alte überdecken, "Seite i/gesamt" setzen.
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    f = basis.schriften()
    x0, y0, x1, y1 = lx["seitenzahl_feld"]
    for i in range(gesamt):
        c.setFillColorRGB(1, 1, 1)
        c.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)
        c.setFillColorRGB(*TEXT)
        c.setFont(f["normal"], 7)
        c.drawRightString(lx["seitenzahl_x_rechts"], lx["seitenzahl_y"], f"Seite {i + 1}/{gesamt}")
        c.showPage()
    c.save()
    over = pikepdf.open(io.BytesIO(buf.getvalue()))
    for i, seite in enumerate(ziel.pages):
        seite.add_overlay(over.pages[i])

    out = io.BytesIO()
    ziel.save(out, compress_streams=True)
    return out.getvalue(), info


def lexware_nummer(lexware_pdf: bytes) -> str | None:
    """Liest die LS-Nummer aus dem Lexware-PDF (z. B. 'LS260067')."""
    text = PdfReader(io.BytesIO(lexware_pdf)).pages[0].extract_text() or ""
    m = re.search(r"\bLS\d{6,}\b", text)
    return m.group(0) if m else None
