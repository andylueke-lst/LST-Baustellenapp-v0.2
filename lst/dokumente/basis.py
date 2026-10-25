"""Gemeinsame Bausteine für alle Dokumente: Konfiguration, Schriften,
Briefpapier, Fußzeile, Textumbruch, Bilder und Dateinamen."""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime
from pathlib import Path

import pikepdf
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[2]
BREITE, HOEHE = A4  # 595.27 x 841.89 pt
RAND_L, RAND_R = 71, 71  # wie die Lexware-Seiten
GRAU = (0.4, 0.4, 0.4)
TEXT = (0.13, 0.13, 0.13)

_cfg: dict | None = None
_schrift: dict[str, str] = {}


def config(pfad: str | Path | None = None) -> dict:
    """Lädt config/firma.json (einmal, danach aus dem Zwischenspeicher)."""
    global _cfg
    if _cfg is None or pfad:
        p = Path(pfad) if pfad else ROOT / "config" / "firma.json"
        _cfg = json.loads(p.read_text(encoding="utf-8"))
    return _cfg


def schriften() -> dict[str, str]:
    """Registriert die erste vorhandene Schrift je Schnitt (Verdana auf dem Mac,
    sonst DejaVu Sans). Fällt notfalls auf Helvetica zurück."""
    if _schrift:
        return _schrift
    cfg = config()
    for schnitt, name in (("normal", "LST"), ("fett", "LST-Fett")):
        for pfad in cfg["schriften"][schnitt]:
            if Path(pfad).exists():
                pdfmetrics.registerFont(TTFont(name, pfad))
                _schrift[schnitt] = name
                break
        else:
            _schrift[schnitt] = "Helvetica-Bold" if schnitt == "fett" else "Helvetica"
    return _schrift


# ---------------------------------------------------------------- Datum, Namen
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def datum(iso: str) -> str:
    return d(iso).strftime("%d.%m.%Y")


def datum_lang(iso: str) -> str:
    x = d(iso)
    return f"{x.day}. {MONATE[x.month - 1]} {x.year}"


def zeitraum(von: str, bis: str) -> str:
    return datum(von) if von == bis else f"{datum(von)} – {datum(bis)}"


def nummer_ziffern(ls_nummer: str | None) -> str:
    """'LS260067' -> '260067'. Grundlage für FD/FB/TB-Nummern."""
    m = re.search(r"(\d+)", ls_nummer or "")
    return m.group(1) if m else "000000"


def dateiname(datum_iso: str, kunde: str, einsatzort: str, dokument: str, nummer: str) -> str:
    """Schema: JJMMTT Kunde Einsatzort Dokumentenname Dokumentennummer.pdf
    Ist Kunde gleich Einsatzort, steht der Name nur einmal."""
    teile = [d(datum_iso).strftime("%y%m%d"), kunde.strip()]
    if einsatzort.strip() and einsatzort.strip() != kunde.strip():
        teile.append(einsatzort.strip())
    teile += [dokument, nummer]
    name = " ".join(t for t in teile if t)
    name = re.sub(r'[\\/:*?"<>|]+', "-", name)  # in Dateinamen verbotene Zeichen
    return re.sub(r"\s+", " ", name).strip() + ".pdf"


def pruefsumme(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


# ---------------------------------------------------------------- Bilder
def foto_jpeg(quelle: str | Path | bytes, kante: int, qualitaet: int) -> tuple[bytes, int, int]:
    """Dreht nach EXIF, verkleinert auf die längste Kante und speichert als JPEG."""
    im = Image.open(io.BytesIO(quelle) if isinstance(quelle, bytes) else quelle)
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((kante, kante), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=qualitaet, optimize=True, progressive=True)
    return buf.getvalue(), im.width, im.height


# ---------------------------------------------------------------- Seiten-Layout
class Seite:
    """Dünne Hülle um den reportlab-Canvas: Textfluss von oben nach unten,
    automatischer Seitenwechsel, Fußzeile über die Briefpapier-Funktion."""

    def __init__(self, ziel: io.BytesIO, oben_erst: float = 190, oben_folge: float = 130,
                 unten: float = 88, titel_folge: str = ""):
        self.c = canvas.Canvas(ziel, pagesize=A4)
        self.f = schriften()
        self.oben_erst, self.oben_folge, self.unten = oben_erst, oben_folge, unten
        self.titel_folge = titel_folge
        self.seiten = 1
        self.y = HOEHE - oben_erst
        self.breite = BREITE - RAND_L - RAND_R

    # -- Grundlagen
    def neue_seite(self):
        self.c.showPage()
        self.seiten += 1
        self.y = HOEHE - self.oben_folge
        if self.titel_folge:
            self.text(self.titel_folge, groesse=8, farbe=GRAU)
            self.abstand(6)

    def platz(self, hoehe: float):
        if self.y - hoehe < self.unten:
            self.neue_seite()

    def abstand(self, pt: float):
        self.y -= pt

    def umbrechen(self, text: str, groesse: float, fett=False, breite=None) -> list[str]:
        font = self.f["fett" if fett else "normal"]
        breite = breite or self.breite
        zeilen = []
        for absatz in str(text).split("\n"):
            wort_zeile = ""
            for wort in absatz.split(" "):
                probe = (wort_zeile + " " + wort).strip()
                if pdfmetrics.stringWidth(probe, font, groesse) <= breite:
                    wort_zeile = probe
                else:
                    if wort_zeile:
                        zeilen.append(wort_zeile)
                    wort_zeile = wort
            zeilen.append(wort_zeile)
        return zeilen

    def text(self, text: str, groesse=9, fett=False, farbe=TEXT, x=None, breite=None,
             zeilenabstand=1.45):
        x = RAND_L if x is None else x
        for zeile in self.umbrechen(text, groesse, fett, breite):
            self.platz(groesse * zeilenabstand)
            self.y -= groesse * zeilenabstand
            self.c.setFont(self.f["fett" if fett else "normal"], groesse)
            self.c.setFillColorRGB(*farbe)
            self.c.drawString(x, self.y, zeile)

    def feld(self, schluessel: str, wert: str, groesse=9, spalte=110):
        """Zeile 'Schlüssel   Wert' wie in der Lexware-Vorlage."""
        zeilen = self.umbrechen(wert or "–", groesse, breite=self.breite - spalte)
        self.platz(groesse * 1.45 * len(zeilen))
        start = self.y
        self.c.setFont(self.f["normal"], groesse)
        self.c.setFillColorRGB(*GRAU)
        self.c.drawString(RAND_L, start - groesse * 1.45, schluessel)
        self.text(wert or "–", groesse, x=RAND_L + spalte, breite=self.breite - spalte)

    def kaestchen(self, x: float, y: float, an: bool, g: float = 8):
        c = self.c
        c.setLineWidth(0.6)
        c.setStrokeColorRGB(*TEXT)
        c.rect(x, y, g, g)
        if an:
            c.line(x + 1.5, y + 1.5, x + g - 1.5, y + g - 1.5)
            c.line(x + 1.5, y + g - 1.5, x + g - 1.5, y + 1.5)

    def auswahl(self, schluessel: str, optionen: list[str], gewaehlt: set[str], spalte=110, groesse=9,
                zusatz: str = ""):
        """Ankreuzzeile wie '☒ CaCO3 ultrafein  ☐ CaCO3 fein'."""
        self.platz(groesse * 1.6)
        self.y -= groesse * 1.45
        c = self.c
        c.setFont(self.f["normal"], groesse)
        c.setFillColorRGB(*GRAU)
        c.drawString(RAND_L, self.y, schluessel)
        x = RAND_L + spalte
        for o in optionen:
            self.kaestchen(x, self.y - 0.5, o in gewaehlt)
            c.setFillColorRGB(*TEXT)
            c.drawString(x + 12, self.y, o)
            x += 12 + pdfmetrics.stringWidth(o, self.f["normal"], groesse) + 16
        if zusatz:
            c.drawString(x, self.y, zusatz)

    def bild(self, jpeg: bytes, x: float, y_oben: float, w: float, h: float, bw: int, bh: int):
        """Bild eingepasst in die Box, zentriert. y_oben = Oberkante der Box."""
        s = min(w / bw, h / bh)
        dw, dh = bw * s, bh * s
        self.c.drawImage(ImageReader(io.BytesIO(jpeg)), x + (w - dw) / 2, y_oben - h + (h - dh) / 2,
                         dw, dh)

    def fertig(self) -> int:
        self.c.save()
        return self.seiten


# ---------------------------------------------------------------- Briefpapier
def _form_aus_seite(ziel: pikepdf.Pdf, quelle: pikepdf.Page):
    return ziel.copy_foreign(quelle.as_form_xobject())


def _umbruch_mit_bindestrich(text: str, font: str, groesse: float, breite: float) -> list[str]:
    """Bricht wie Lexware: an Leerzeichen, notfalls nach einem Bindestrich im Wort."""
    zeilen, zeile = [], ""
    for wort in text.split(" "):
        probe = (zeile + " " + wort).strip()
        if pdfmetrics.stringWidth(probe, font, groesse) <= breite:
            zeile = probe
            continue
        teile = [m.end() for m in re.finditer("-", wort)]
        geteilt = False
        for pos in reversed(teile):
            vorn = (zeile + " " + wort[:pos]).strip()
            if pdfmetrics.stringWidth(vorn, font, groesse) <= breite:
                zeilen.append(vorn)
                zeile = wort[pos:]
                geteilt = True
                break
        if not geteilt:
            if zeile:
                zeilen.append(zeile)
            zeile = wort
    if zeile:
        zeilen.append(zeile)
    return zeilen


def fusszeile_zeichnen(c: canvas.Canvas, seite: int, gesamt: int, links_unten: str = "", wie_lexware=False):
    """Fußzeile im Stil der Lexware-Belege: zentrierte Zeilen, rechts die Seitenzahl.
    wie_lexware: gleicher Umbruch und Zeilenabstand wie auf den Lexware-Seiten."""
    f = schriften()
    cfg = config()
    c.setFillColorRGB(*TEXT)
    if wie_lexware:
        zeilen = [z for zeile in cfg["fusszeile"] for z in _umbruch_mit_bindestrich(zeile, f["normal"], 7.25, 456)]
        y = 46
        for z in zeilen:
            c.setFont(f["normal"], 7.25)
            c.drawCentredString(BREITE / 2, y, z)
            y -= 8.4
    else:
        y = 46
        for zeile in cfg["fusszeile"]:
            groesse = 7
            while pdfmetrics.stringWidth(zeile, f["normal"], groesse) > BREITE - 40 and groesse > 6:
                groesse -= 0.25
            c.setFont(f["normal"], groesse)
            c.drawCentredString(BREITE / 2, y, zeile)
            y -= 9.5
    c.setFont(f["normal"], 7)
    c.drawRightString(cfg["lexware"]["seitenzahl_x_rechts"], cfg["lexware"]["seitenzahl_y"],
                      f"Seite {seite}/{gesamt}")
    if links_unten:
        c.setFillColorRGB(*GRAU)
        c.setFont(f["normal"], 6)
        c.drawString(RAND_L, cfg["lexware"]["seitenzahl_y"], links_unten)


def mit_briefpapier(inhalt_pdf: bytes, links_unten: str = "", fusszeile=True, immer_erstseite=False,
                    wie_lexware=False) -> bytes:
    """Legt das Briefpapier unter jede Seite (Erstseite mit Spruch, Folgeseiten nur Logo)
    und ergänzt Fußzeile und Seitenzahlen."""
    cfg = config()
    bp = cfg["briefpapier"]
    gesamt = len(pikepdf.open(io.BytesIO(inhalt_pdf)).pages)
    if fusszeile:
        inhalt_pdf = _ueberlagern(inhalt_pdf, lambda c, i: fusszeile_zeichnen(c, i + 1, gesamt, links_unten, wie_lexware))
    ziel = pikepdf.open(io.BytesIO(inhalt_pdf))
    erst = pikepdf.open(ROOT / bp["erstseite"])
    folge = pikepdf.open(ROOT / bp["folgeseite"])
    f_erst = _form_aus_seite(ziel, erst.pages[0])
    f_folge = _form_aus_seite(ziel, folge.pages[0])
    for i, seite in enumerate(ziel.pages):
        form, aus = (f_erst, bp["erstseite_ausschnitte"]) if i == 0 or immer_erstseite else (f_folge, bp["folgeseite_ausschnitte"])
        if wie_lexware and form is f_erst:
            # Logo und Spruch so setzen wie auf den Lexware-Seiten (dort etwas anders skaliert)
            for k, teil in enumerate(bp["lexware_angleichung"]):
                unterlegen(ziel, seite, form, [teil["ausschnitt"]], f"LSTBriefpapier{k}", teil["matrix"])
        else:
            unterlegen(ziel, seite, form, aus, "LSTBriefpapier")
    out = io.BytesIO()
    ziel.save(out, compress_streams=True)
    return out.getvalue()


def unterlegen(pdf: pikepdf.Pdf, seite: pikepdf.Page, form, ausschnitte, name: str, matrix=None):
    res = seite.obj.get(pikepdf.Name.Resources)
    if res is None:
        seite.obj[pikepdf.Name.Resources] = res = pikepdf.Dictionary()
    xo = res.get(pikepdf.Name.XObject)
    if xo is None:
        res[pikepdf.Name.XObject] = xo = pikepdf.Dictionary()
    xo[pikepdf.Name("/" + name)] = form
    if ausschnitte:
        clip = " ".join(f"{x0} {y0} {x1 - x0} {y1 - y0} re" for x0, y0, x1, y1 in ausschnitte)
        ops = f"q {clip} W n /{name} Do Q\n"
    else:
        ops = f"q /{name} Do Q\n"
    if matrix:
        ops = "q " + " ".join(str(v) for v in matrix) + " cm " + ops + "Q\n"
    seite.contents_add(pikepdf.Stream(pdf, ops.encode()), prepend=True)


def _ueberlagern(pdf_bytes: bytes, zeichne) -> bytes:
    """Zeichnet mit reportlab auf jede Seite (z. B. Fußzeile) und legt es oben drauf."""
    ziel = pikepdf.open(io.BytesIO(pdf_bytes))
    n = len(ziel.pages)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i in range(n):
        zeichne(c, i)
        c.showPage()
    c.save()
    over = pikepdf.open(io.BytesIO(buf.getvalue()))
    for i, seite in enumerate(ziel.pages):
        seite.add_overlay(over.pages[i])
    out = io.BytesIO()
    ziel.save(out)
    return out.getvalue()


def jetzt_text() -> str:
    return datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")
