"""Erzeugt beim Job-Abschluss alle Dokumente aus einem Einsatz.

Der Einsatz hat dieselbe Form wie im Prototyp (einsatzort, kunde, ls, tage[],
anlagen[], fotos[], team, geraete). Reihenfolge: zuerst die Fotodoku, damit
ihre Prüfsumme auf den Lieferschein kommt.
"""
from __future__ import annotations

from datetime import date

from . import basis, fotos, lieferschein
from .lieferschein import STRAHLMITTEL


def _minuten(t: str | None) -> int | None:
    if not t:
        return None
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _vor_ort(z: dict) -> str | None:
    a, b = _minuten(z["p"].get("3")), _minuten(z["p"].get("4"))
    if a is None or b is None:
        return None
    if b < a:  # Nachtschicht über Mitternacht
        b += 1440
    m = b - a - (z.get("pause") or 0)
    return f"{m // 60}:{m % 60:02d} h"


def einsatz_dokumente(e: dict, stamm: dict, foto_quelle, lexware_pdf: bytes | None, sig: dict,
                      heute: str | None = None) -> dict:
    """Liefert {'fotodoku': (dateiname, bytes, info), 'lieferschein': (...)}.

    stamm: mitarbeiter{id: name}, strahlgeraete{id: {name, typ}}, typen{typ: {felder[]}}
    foto_quelle: Funktion foto_id -> Pfad oder Bytes
    sig: name, bild (PNG-Bytes), eingesehen, zeiten_auf_ls, ohne_kunde
    """
    heute = heute or date.today().isoformat()
    ls = e.get("ls") or (lexware_pdf and lieferschein.lexware_nummer(lexware_pdf)) or None
    ziffern = basis.nummer_ziffern(ls)
    tage = e.get("tage") or []
    jahr = basis.d(tage[0]["datum"] if tage else e["von"]).year

    # ---- Fotodoku
    fd_nr = "FD" + ziffern
    anlagen = []
    for a in e["anlagen"]:
        anlagen.append({"name": a["name"],
                        "vorher": [foto_quelle(f["id"]) for f in e["fotos"] if f["anlage"] == a["id"] and f["phase"] == "vorher"],
                        "nachher": [foto_quelle(f["id"]) for f in e["fotos"] if f["anlage"] == a["id"] and f["phase"] == "nachher"]})
    von = tage[0]["datum"] if tage else e["von"]
    bis = tage[-1]["datum"] if tage else e["bis"]
    untertitel = f"{e.get('reinigungsart', '')} {basis.datum_lang(von)}" if von == bis else \
        f"{e.get('reinigungsart', '')} {basis.datum(von)} – {basis.datum_lang(bis)}"
    fd_daten = {"titel": f"{e['einsatzort']} {jahr}", "untertitel": untertitel.strip(), "nummer": fd_nr,
                "ls": ls, "kunde": e.get("kunde"), "einsatzort": e["einsatzort"], "anlagen": anlagen}
    fd_pdf = fotos.fotodoku(fd_daten)
    fd_hash = basis.pruefsumme(fd_pdf)

    # ---- Lieferschein, Seite Arbeitsnachweis
    ma = stamm.get("mitarbeiter", {})
    arten = sorted({t["bericht"].get("sm") for t in tage if t["bericht"].get("sm")})
    grund = all(t["bericht"].get("grund", True) for t in tage)
    kg = sum(float(t["bericht"].get("kg") or 0) for t in tage if not t["bericht"].get("grund", True))
    geraete = []
    for sid in e["geraete"].get("strahl", []):
        g = stamm["strahlgeraete"][sid]
        letzte = next((t["bericht"]["einst"][sid] for t in reversed(tage) if t["bericht"].get("einst", {}).get(sid)), {})
        geraete.append({"name": g["name"], "felder": [
            {"label": fd["label"], "werte": fd["werte"], "wert": letzte.get(fd["key"])}
            for fd in stamm["typen"][g["typ"]]["felder"]]})
    zeiten = None
    if sig.get("zeiten_auf_ls"):
        zeiten = []
        for t in tage:
            z = t["zeiten"].get(e["team"]["vor"])
            if z:
                zeiten.append({"datum": t["datum"], "ankunft": z["p"].get("3"), "abfahrt": z["p"].get("4"),
                               "vor_ort": _vor_ort(z)})
    unterschrift = {"ohne_kunde": True} if sig.get("ohne_kunde") else \
        {"name": sig.get("name", ""), "zeit": sig.get("zeit") or basis.jetzt_text(), "bild": sig.get("bild")}
    ls_daten = {
        "ls": ls, "einsatzort": e["einsatzort"], "adresse": e.get("adresse", ""), "kunde": e.get("kunde"),
        "tage": [{"datum": t["datum"], "text": t["bericht"].get("text"), "bes": t["bericht"].get("bes"),
                  "unterschrieben": bool(t.get("unterschrift"))} for t in tage],
        "vorarbeiter": ma.get(e["team"].get("vor")), "facharbeiter": ma.get(e["team"].get("fach")),
        "strahlmittel": {"arten": arten, "grundmenge": grund, "kg": kg},
        "geraete": geraete, "zeiten": zeiten,
        "fotodoku": {"nummer": fd_nr, "anzahl": len(e["fotos"]), "eingesehen": bool(sig.get("eingesehen")),
                     "pruefsumme": fd_hash},
        "unterschrift": unterschrift,
    }
    ls_pdf, ls_info = lieferschein.zusammenfuegen(lexware_pdf, ls_daten)

    kunde, ort = e.get("kunde") or e["einsatzort"], e["einsatzort"]
    return {
        "fotodoku": (basis.dateiname(heute, kunde, ort, "Fotodoku", fd_nr), fd_pdf, {"pruefsumme": fd_hash}),
        "lieferschein": (basis.dateiname(heute, kunde, ort, "Lieferschein unterschr" if not sig.get("ohne_kunde")
                                         else "Lieferschein", ls or ""), ls_pdf, ls_info),
    }


def fotobericht_aus_auswahl(e: dict, foto_ids: list[str], foto_quelle, begleittext: str = "",
                            heute: str | None = None) -> tuple[str, bytes, dict]:
    """Fotobericht für den Auftraggeber aus den im Büro gewählten Fotos."""
    heute = heute or date.today().isoformat()
    ls = e.get("ls")
    nr = "FB" + basis.nummer_ziffern(ls)
    gewaehlt = set(foto_ids)
    anlagen = [{"name": a["name"],
                "vorher": [foto_quelle(f["id"]) for f in e["fotos"] if f["anlage"] == a["id"] and f["phase"] == "vorher" and f["id"] in gewaehlt],
                "nachher": [foto_quelle(f["id"]) for f in e["fotos"] if f["anlage"] == a["id"] and f["phase"] == "nachher" and f["id"] in gewaehlt]}
               for a in e["anlagen"]]
    tage = e.get("tage") or []
    von = tage[0]["datum"] if tage else e["von"]
    bis = tage[-1]["datum"] if tage else e["bis"]
    untertitel = f"{e.get('reinigungsart', '')} {basis.datum_lang(von)}" if von == bis else \
        f"{e.get('reinigungsart', '')} {basis.datum(von)} – {basis.datum_lang(bis)}"
    daten = {"titel": f"{e['einsatzort']} {basis.d(von).year}", "untertitel": untertitel.strip(), "nummer": nr,
             "ls": ls, "kunde": e.get("kunde"), "einsatzort": e["einsatzort"], "anlagen": anlagen,
             "begleittext": begleittext, "leere_phase_ausblenden": True}
    pdf, info = fotos.fotobericht(daten)
    kunde = e.get("kunde") or e["einsatzort"]
    return basis.dateiname(heute, kunde, e["einsatzort"], "Fotobericht", nr), pdf, info
