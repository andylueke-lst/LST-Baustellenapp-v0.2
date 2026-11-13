"""Übernahme aus der alten, eigenständigen iPad-App (Prototyp ohne Server).

Die alte App liefert zwei Teile:
- „Notiz › Alles als Datei teilen“: JSON mit allen Einsätzen (Zeiten, Berichte, Abbau-Check,
  Unterschrift), aber ohne Fotos
- „Fotos teilen“: JPEGs mit Namen  JJMMTT Einsatzort Anlage vorher|nachher NN.jpg

Daraus wird ein Einsatz auf dem Server. Ist er in der alten App abgeschlossen, entstehen
Lieferschein und Fotodoku wie bei einem Abschluss auf dem iPad (ohne Mailversand an den Kunden).
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import speicher

VERBOTEN = re.compile(r'[\\/:*?"<>|]')
FOTO = re.compile(r"^(\d{6}) (.*) (vorher|nachher) (\d+)(?: \d+)?$", re.I)


def einsaetze_aus_datei(roh: bytes) -> list[dict]:
    d = json.loads(roh.decode("utf-8"))
    if isinstance(d, dict) and isinstance(d.get("einsaetze"), list):
        return [e for e in d["einsaetze"] if isinstance(e, dict) and e.get("id")]
    if isinstance(d, dict) and isinstance(d.get("einsatz"), dict):
        return [d["einsatz"]]
    raise ValueError("Keine Einsätze in der Datei gefunden.")


def _jjmmtt(iso: str) -> str:
    try:
        return date.fromisoformat(iso[:10]).strftime("%y%m%d")
    except (TypeError, ValueError):
        return ""


def foto_zuordnen(e: dict, dateiname: str) -> dict | None:
    """Ordnet ein geteiltes Foto über den Dateinamen einer Anlage, Phase und einem Tag zu."""
    stamm = dateiname.rsplit(".", 1)[0].strip()
    m = FOTO.match(stamm)
    if not m:
        return None
    tag_kurz, rest, phase = m.group(1), m.group(2), m.group(3).lower()
    anlage = None
    # längste passende Anlage zuerst (falls ein Name im anderen steckt)
    for a in sorted(e.get("anlagen") or [], key=lambda a: -len(a.get("name", ""))):
        name = VERBOTEN.sub("-", a.get("name") or "Anlage")
        if rest == name or rest.endswith(" " + name):
            anlage = a["id"]
            break
    if not anlage:
        return None
    tage = e.get("tage") or []
    tag = next((i for i, t in enumerate(tage) if _jjmmtt(t.get("datum", "")) == tag_kurz), 0)
    return {"anlage": anlage, "phase": phase, "tag": tag}


def vorschau(e: dict, dateinamen: list[str]) -> dict:
    zugeordnet = [n for n in dateinamen if foto_zuordnen(e, n)]
    return {"id": e["id"], "einsatzort": e.get("einsatzort", ""), "kunde": e.get("kunde", ""),
            "von": e.get("von", ""), "bis": e.get("bis", ""), "status": e.get("status", ""),
            "tage": len(e.get("tage") or []), "fotos_alt": len(e.get("fotos") or []),
            "fotos_passend": len(zugeordnet), "unterschrift": bool((e.get("sig") or {}).get("img")),
            "ohne_kunde": bool((e.get("abschluss") or {}).get("ohneKunde"))}


def uebernehmen(e: dict, fotos: list[tuple[str, bytes]], ziel_id: str | None) -> tuple[str, dict, int, list[str]]:
    """Legt den Einsatz an (oder füllt einen vorhandenen) und speichert die Fotos.
    Gibt (Einsatz-ID, Unterschrift für den Abschluss oder None, Anzahl Fotos, nicht zugeordnete Dateien) zurück."""
    neu = {k: v for k, v in e.items() if k not in ("lsHtml", "sync_abschluss", "serverDocs", "docs", "trash", "fotos", "sig")}
    if ziel_id:
        ziel = speicher.einsatz(ziel_id) or {}
        neu["id"] = ziel_id
        for k in ("notion", "ls", "apKunde", "apOrt", "technisch", "bestellnummer"):
            if ziel.get(k) and not neu.get(k):
                neu[k] = ziel[k]
        if ziel.get("ls"):
            neu["ls"] = ziel["ls"]
    elif speicher.einsatz(neu["id"]):
        neu["id"] = neu["id"] + "-alt"
    for teil in ("start", "ende"):              # „5309,97“ aus dem Textfeld der alten App → Zahl
        for k, v in list((neu.get(teil) or {}).items()):
            if isinstance(v, str):
                try:
                    neu[teil][k] = float(v.replace(".", "").replace(",", ".")) if "," in v else float(v)
                except ValueError:
                    pass
    eid = neu["id"]
    alt_status = e.get("status")
    neu["status"] = "inarbeit" if alt_status == "abgeschlossen" else (alt_status or "vorbereitet")
    neu.pop("abschluss", None)
    neu.pop("ergebnis", None)
    neu["uebernommen_aus_alter_app"] = date.today().isoformat()
    speicher.einsatz_speichern(neu)

    n, fehlt = 0, []
    for name, daten in fotos:
        z = foto_zuordnen(e, name)
        if not z:
            fehlt.append(name)
            continue
        speicher.foto_speichern(eid, daten, z["anlage"], z["phase"], z["tag"])
        n += 1

    sig = None
    if alt_status == "abgeschlossen":
        s = e.get("sig") or {}
        ab = e.get("abschluss") or {}
        sig = {"name": s.get("name") or ab.get("name") or "", "bild": s.get("img"),
               "eingesehen": bool(s.get("eingesehen")), "zeiten_auf_ls": bool(s.get("zeiten", e.get("zeitenAufLs"))),
               "ohne_kunde": bool(ab.get("ohneKunde") or not s.get("img")), "datum": ab.get("datum")}
    speicher.protokoll("altdaten_uebernommen", "buero", eid, fotos=n, nicht_zugeordnet=len(fehlt))
    return eid, sig, n, fehlt
