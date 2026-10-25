"""Fotodokumentation (alle Fotos, Teil des Lieferscheins) und Fotobericht
(Auswahl im Büro, per Mail an den Auftraggeber, unter 10 MB)."""
from __future__ import annotations

import io

from . import basis
from .basis import GRAU, RAND_L, TEXT, Seite, config

SPALT = 12  # Abstand zwischen den Fotos in pt


class ZuGross(Exception):
    pass


def _seite_bauen(daten: dict, art: str, kante: int, qualitaet: int, cache: dict) -> bytes:
    buf = io.BytesIO()
    titel = daten["titel"]
    s = Seite(buf, oben_erst=152, oben_folge=112, unten=100,
              titel_folge=f"{titel} · {art} {daten['nummer']}")
    s.text(titel, 15, fett=True, zeilenabstand=1.3)
    s.abstand(4)
    s.text(daten.get("untertitel", ""), 11, zeilenabstand=1.35)
    bezug = f"{art} {daten['nummer']}"
    if daten.get("ls"):
        bezug += f" · zu Lieferschein {daten['ls']}"
    if daten.get("kunde") and daten["kunde"] != daten.get("einsatzort"):
        bezug += f" · Auftraggeber: {daten['kunde']}"
    s.abstand(2)
    s.text(bezug, 8, farbe=GRAU)
    if daten.get("begleittext"):
        s.abstand(10)
        s.text(daten["begleittext"], 9.5)

    w = (s.breite - SPALT) / 2
    h = w * 0.75

    def jpeg(q):
        key = (str(q), kante, qualitaet)
        if key not in cache:
            cache[key] = basis.foto_jpeg(q, kante, qualitaet)
        return cache[key]

    for anl in daten["anlagen"]:
        fotos = {ph: anl.get(ph) or [] for ph in ("vorher", "nachher")}
        if not fotos["vorher"] and not fotos["nachher"]:
            continue
        s.abstand(16)
        s.platz(16 + 14 + h)  # Überschrift nie allein am Seitenende
        s.text(anl["name"], 12, fett=True, zeilenabstand=1.3)
        for ph, label in (("vorher", "Vorher"), ("nachher", "Nachher")):
            liste = fotos[ph]
            if not liste and daten.get("leere_phase_ausblenden"):
                continue
            s.abstand(6)
            s.platz(14 + (h if liste else 0))
            s.text(label, 9.5, farbe=GRAU)
            if not liste:
                s.text("keine Fotos", 8.5, farbe=GRAU)
                continue
            s.abstand(4)
            for i in range(0, len(liste), 2):
                s.platz(h + SPALT)
                for j, q in enumerate(liste[i:i + 2]):
                    data, bw, bh = jpeg(q)
                    s.bild(data, RAND_L + j * (w + SPALT), s.y, w, h, bw, bh)
                s.y -= h + SPALT
    s.fertig()
    return basis.mit_briefpapier(buf.getvalue(), links_unten=f"{art} {daten['nummer']}")


def fotodoku(daten: dict) -> bytes:
    """Alle Fotos, nach Anlage gruppiert. Wird stufenweise verkleinert, bis sie unter
    der Mail-Grenze liegt, damit sie auch später an den Auftraggeber gehen kann."""
    pdf, _ = _mit_stufen(daten, "Fotodokumentation", streng=False)
    return pdf


def _mit_stufen(daten: dict, art: str, ziel_mb=None, max_mb=None, streng=True) -> tuple[bytes, dict]:
    cfg = config()["fotos"]
    ziel = (ziel_mb or cfg["bericht_ziel_mb"]) * 1024 * 1024
    grenze = (max_mb or cfg["bericht_max_mb"]) * 1024 * 1024
    stufen = [(cfg["kante_px"], cfg["jpeg_qualitaet"]), (1200, 75), (1000, 70), (900, 65), (800, 60)]
    cache: dict = {}
    pdf = b""
    for kante, q in stufen:
        pdf = _seite_bauen(daten, art, kante, q, cache)
        if len(pdf) <= ziel:
            return pdf, {"kante_px": kante, "qualitaet": q, "mb": round(len(pdf) / 1048576, 2)}
    info = {"kante_px": stufen[-1][0], "qualitaet": stufen[-1][1], "mb": round(len(pdf) / 1048576, 2),
            "hinweis": "über der Zielgröße"}
    if len(pdf) <= grenze or not streng:
        return pdf, info
    raise ZuGross(f"Auch stark verkleinert {len(pdf) / 1048576:.1f} MB. Bitte weniger Fotos auswählen.")


def fotobericht(daten: dict, ziel_mb: float | None = None, max_mb: float | None = None) -> tuple[bytes, dict]:
    """Ausgewählte Fotos. Verkleinert stufenweise, bis die Datei unter dem Ziel liegt;
    über der Obergrenze gibt es einen Fehler mit Hinweis."""
    return _mit_stufen(daten, "Fotobericht", ziel_mb, max_mb, streng=True)


def groesse_schaetzen(anzahl_fotos: int, kb_pro_foto: int = 260) -> float:
    """Grobe Schätzung für die Live-Anzeige bei der Auswahl (MB)."""
    return round((anzahl_fotos * kb_pro_foto + 120) / 1024, 1)
