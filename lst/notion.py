"""Notion-Anbindung: Projekte aus „LST Projekte“ als vorbereitete Einsätze übernehmen.

Stand Stufe 5a: nur LESEN. Geschrieben wird in Notion noch nichts. Das Schreiben kommt
gegen die Test-Kopie und wird erst mit "schreiben": true freigeschaltet.

Zugang in <Daten>/geheim.json (nie in Git, nie im Chat):
  "notion": {
    "token": "ntn_…",                                   interne Integration „LST Baustellen-App“
    "projekte": "644d7ac7-3929-4aea-8f1e-4d9cec143d1a", Datenquelle LST Projekte (oder Test-Kopie)
    "schreiben": false
  }
Die Integration bekommt in Notion nur Zugriff auf die freigegebenen Seiten. „LST Projekte
kaufmännisch“ wird ihr nie freigegeben; der Abschnitt „Kaufmännisches“ wird auch beim
Lesen übersprungen.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from . import speicher

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"
STATUS_UEBERNEHMEN = ("13 Schriftlich Bestätigt", "14 Vorbereitung", "15 Umsetzung")


class NotionFehler(Exception):
    pass


def einstellungen() -> dict:
    return speicher.geheim().get("notion") or {}


def eingerichtet() -> bool:
    n = einstellungen()
    return bool(n.get("token") and n.get("projekte"))


# ---------------------------------------------------------------- HTTP
def _anfrage(methode: str, pfad: str, daten: dict | None = None) -> dict:
    n = einstellungen()
    if not n.get("token"):
        raise NotionFehler("Notion ist noch nicht eingerichtet (Schlüssel fehlt in geheim.json).")
    if methode != "GET" and not pfad.endswith("/query") and not n.get("schreiben"):
        raise NotionFehler("Schreiben in Notion ist noch nicht freigegeben.")
    req = urllib.request.Request(API + pfad, method=methode,
                                 data=json.dumps(daten).encode() if daten is not None else None,
                                 headers={"Authorization": f"Bearer {n['token']}", "Notion-Version": VERSION,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            text = json.loads(e.read()).get("message", "")
        except ValueError:
            text = ""
        if e.code == 404:
            text = "Seite nicht gefunden oder nicht für die Integration freigegeben. " + text
        raise NotionFehler(f"Notion {e.code}: {text}".strip()) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise NotionFehler(f"Notion nicht erreichbar: {e}") from None


def seite(pid: str) -> dict:
    return _anfrage("GET", f"/pages/{pid}")


def kinder(block_id: str) -> list[dict]:
    aus, cursor = [], None
    while True:
        d = _anfrage("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        aus += d.get("results", [])
        if not d.get("has_more"):
            return aus
        cursor = d.get("next_cursor")


# ---------------------------------------------------------------- Werte aus Notion-Eigenschaften
def _text(rt: list | None) -> str:
    return "".join(t.get("plain_text", "") for t in (rt or []))


def wert(p: dict | None):
    """Einfacher Python-Wert aus einer Notion-Eigenschaft."""
    if not p:
        return None
    t = p.get("type")
    v = p.get(t)
    if t in ("title", "rich_text"):
        return _text(v)
    if t in ("select", "status"):
        return (v or {}).get("name")
    if t == "multi_select":
        return [o["name"] for o in v or []]
    if t == "date":
        return v
    if t in ("email", "phone_number", "url", "number", "checkbox"):
        return v
    if t == "relation":
        return [r["id"] for r in v or []]
    if t == "people":
        return [x.get("name") or x.get("id") for x in v or []]
    if t == "rollup":
        if (v or {}).get("type") == "array":
            vals = [wert(x) for x in v.get("array", [])]
            flach = []
            for x in vals:
                flach += x if isinstance(x, list) else [x]
            return [x for x in flach if x not in (None, "")]
        return (v or {}).get((v or {}).get("type"))
    if t == "formula":
        return (v or {}).get((v or {}).get("type"))
    return None


def eig(seite_json: dict, name: str):
    return wert((seite_json.get("properties") or {}).get(name))


def _erstes(x):
    if isinstance(x, list):
        return x[0] if x else ""
    return x or ""


def titel(seite_json: dict) -> str:
    for p in (seite_json.get("properties") or {}).values():
        if p.get("type") == "title":
            return _text(p.get("title"))
    return ""


# ---------------------------------------------------------------- Projekte lesen
def projekte_abfragen(tage_zurueck: int = 7, tage_voraus: int = 60) -> list[dict]:
    """Bestätigte Projekte im Zeitfenster, früheste zuerst."""
    ds = einstellungen()["projekte"]
    von = (date.today() - timedelta(days=tage_zurueck)).isoformat()
    bis = (date.today() + timedelta(days=tage_voraus)).isoformat()
    filt = {"and": [
        {"or": [{"property": "LST Status externe Projekte", "status": {"equals": s}} for s in STATUS_UEBERNEHMEN]},
        {"property": "Umsetzung", "date": {"on_or_after": von}},
        {"property": "Umsetzung", "date": {"on_or_before": bis}}]}
    aus, cursor = [], None
    while True:
        body = {"filter": filt, "sorts": [{"property": "Umsetzung", "direction": "ascending"}], "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        d = _anfrage("POST", f"/data_sources/{ds}/query", body)
        aus += d.get("results", [])
        if not d.get("has_more"):
            return aus
        cursor = d.get("next_cursor")


def kurzinfo(p: dict) -> dict:
    """Für die Liste im Büro, ohne weitere Anfragen."""
    um = eig(p, "Umsetzung") or {}
    return {"id": p["id"], "name": eig(p, "Projektname") or titel(p), "von": (um.get("start") or "")[:10],
            "bis": (um.get("end") or um.get("start") or "")[:10], "status": eig(p, "LST Status externe Projekte"),
            "reinigungsart": eig(p, "Reinigungsart"), "url": p.get("url")}


def _firma(pid: str | None) -> dict:
    if not pid:
        return {}
    try:
        f = seite(pid)
    except NotionFehler:
        return {}   # Firmenadressen nicht freigegeben: Rollups der Projektseite reichen meist
    name = " ".join(x for x in (eig(f, "Firmenname 1"), eig(f, "Firmenname 2")) if x) or titel(f)
    plz_ort = ""
    plz_rel = eig(f, "PLZ Straße") or []
    if plz_rel:
        try:
            pz = seite(plz_rel[0])
            plz_ort = " ".join(x for x in (titel(pz), _erstes(eig(pz, "Ort"))) if x)
        except NotionFehler:
            pass
    return {"name": name, "suchname": titel(f), "strasse": eig(f, "Straße") or "", "plz_ort": plz_ort,
            "ort_einsatz": eig(f, "Ort Einsatz") or "", "telefon": eig(f, "Telefon") or "", "mail": eig(f, "E–Mail–Adresse") or ""}


def _kontakt(ids: list[str] | None) -> dict:
    if not ids:
        return {}
    try:
        k = seite(ids[0])
    except NotionFehler:
        return {}
    pr = k.get("properties") or {}
    mail = next((wert(v) for v in pr.values() if v.get("type") == "email" and wert(v)), "")
    tel = next((wert(v) for v in pr.values() if v.get("type") == "phone_number" and wert(v)), "")
    # LST Personen: Vorname/Nachname/Titel, sonst der Kurzname (Titel der Seite)
    voll = " ".join(x for x in (eig(k, "Titel"), eig(k, "Vorname"), eig(k, "Nachname")) if x)
    mail = eig(k, "E–Mail geschäftlich") or mail
    tel = eig(k, "Mobil geschäftlich") or eig(k, "Durchwahl") or tel
    return {"name": voll or titel(k), "mail": mail or "", "tel": tel or ""}


# ---------------------------------------------------------------- Seiteninhalt: Technische Daten, Lieferschein
def _block_text(b: dict) -> str:
    v = b.get(b.get("type"), {})
    return _text(v.get("rich_text"))


def _suche(blocks: list[dict], name: str, tiefe: int = 0) -> dict | None:
    """Sucht einen Block (Überschrift/Toggle) mit genau diesem Text, auch in synchronisierten Blöcken.
    Kaufmännisches wird nie geöffnet."""
    for b in blocks:
        t = _block_text(b).strip()
        if t.lower().startswith("kaufmännisch"):
            continue
        if t == name:
            return b
        if b.get("has_children") and tiefe < 4 and (b["type"] in ("synced_block", "toggle", "column_list", "column")
                                                     or b["type"].startswith("heading")):
            quelle = b["id"]
            if b["type"] == "synced_block" and (b.get("synced_block") or {}).get("synced_from"):
                # Verweise auf synchronisierte Blöcke anderer Seiten (z. B. „Kaufmännisches“) nie öffnen.
                # Der Inhalt der Projektseite steht im Original-Block der Seite selbst.
                continue
            try:
                treffer = _suche(kinder(quelle), name, tiefe + 1)
            except NotionFehler:
                treffer = None
            if treffer:
                return treffer
    return None


def _alle_texte(block_id: str) -> str:
    zeilen = []
    for b in kinder(block_id):
        t = _block_text(b)
        if t:
            zeilen.append(t)
    return "\n".join(zeilen)


def technische_daten(seiten_blocks: list[dict]) -> str:
    b = _suche(seiten_blocks, "Technische Daten Wärmetauscher")
    if not b:
        return ""
    text = _alle_texte(b["id"])
    # Nur ausgefüllte Zeilen „Feld: Wert“ behalten, leere Vorlagenzeilen weglassen
    gefuellt = [z.strip() for z in text.splitlines() if ":" in z and z.split(":", 1)[1].strip()]
    return "\n".join(gefuellt)


def anlagen_vorschlag(technik: str) -> list[str]:
    """Aus „Anzahl Wärmetauscher: 2“ und „Art der Wärmetauscher: Gaskühler“ → Gaskühler 1, Gaskühler 2.
    Das Büro prüft die Liste vor dem Einsatz."""
    felder = {k.strip().lower(): v.strip() for k, v in (z.split(":", 1) for z in technik.splitlines() if ":" in z)}
    art = felder.get("art der wärmetauscher") or "Wärmetauscher"
    m = re.search(r"\d+", felder.get("anzahl wärmetauscher", ""))
    n = int(m.group()) if m else 1
    n = max(1, min(n, 40))
    return [art] if n == 1 else [f"{art} {i}" for i in range(1, n + 1)]


def lieferschein_datei(seiten_blocks: list[dict]) -> tuple[str, str] | None:
    """PDF unter Projektinfos → Unterlagen → Lieferschein (nicht im kaufmännischen Teil)."""
    pi = _suche(seiten_blocks, "Projektinfos")
    if not pi:
        return None
    unt = _suche(kinder(pi["id"]), "Unterlagen")
    if not unt:
        return None
    ls = _suche(kinder(unt["id"]), "Lieferschein")
    if not ls or not ls.get("has_children"):
        return None
    for b in kinder(ls["id"]):
        if b.get("type") in ("file", "pdf"):
            f = b[b["type"]]
            url = (f.get("file") or f.get("external") or {}).get("url")
            name = f.get("name") or _text(f.get("caption")) or "lieferschein.pdf"
            if url and (b["type"] == "pdf" or name.lower().endswith(".pdf")):
                return name, url
    return None


def herunterladen(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


# ---------------------------------------------------------------- Projekt → Einsatz
def _person_id(vorname: str | None) -> str | None:
    if not vorname:
        return None
    vn = vorname.strip().split()[0].lower()
    return next((m["id"] for m in speicher.stamm("mitarbeiter", []) if m.get("vorname", "").lower() == vn), None)


def als_einsatz(p: dict, alt: dict | None = None) -> tuple[dict, list[dict]]:
    """Baut aus einer Notion-Projektseite einen Einsatz (Form wie in der App).
    Vorhandene Einsätze behalten Anlagen-IDs, Team, Geräte und alles vom iPad."""
    um = eig(p, "Umsetzung") or {}
    von = (um.get("start") or date.today().isoformat())[:10]
    bis = (um.get("end") or von)[:10]
    kunde_f = _firma(_erstes(eig(p, "Kunde")) or None)
    ort_f = _firma(_erstes(eig(p, "Einsatz bei")) or None)
    ap_kunde = _kontakt(eig(p, "AP beim Kunden"))
    ap_ort = _kontakt(eig(p, "AP Einsatzort"))
    einsatzort = _erstes(eig(p, "Einsatzort")) or ort_f.get("suchname") or ort_f.get("name") or eig(p, "Projektname")
    strasse = _erstes(eig(p, "Einsatzstraße")) or ort_f.get("strasse", "")
    adresse = ", ".join(x for x in (strasse, ort_f.get("plz_ort") or ort_f.get("ort_einsatz")) if x)
    blocks = kinder(p["id"])
    technik = technische_daten(blocks)
    zweiter = (eig(p, "2. Mann") or [None])[0]
    e = dict(alt or {})
    e.update({
        "id": (alt or {}).get("id") or "n" + p["id"].replace("-", "")[:11],
        "notion": {"id": p["id"], "url": p.get("url"), "status": eig(p, "LST Status externe Projekte"),
                   "geholt": date.today().isoformat(), "projektname": eig(p, "Projektname")},
        "art": "kunde", "status": (alt or {}).get("status", "vorbereitet"),
        "einsatzort": einsatzort, "kunde": kunde_f.get("name") or einsatzort, "adresse": adresse,
        "apOrt": {"name": ap_ort.get("name", ""), "tel": _erstes(eig(p, "Mobil AP Einsatzort")) or _erstes(eig(p, "Telefon AP Einsatzort")) or ap_ort.get("tel", ""),
                  "mail": _erstes(eig(p, "Email AP Einsatzort")) or ap_ort.get("mail", "")},
        "apKunde": {"name": ap_kunde.get("name", ""), "mail": _erstes(eig(p, "Email Kunde")) or ap_kunde.get("mail", "")},
        "von": von, "bis": max(von, bis), "reinigungsart": eig(p, "Reinigungsart") or "",
        "bestellnummer": eig(p, "Bestellnummer") or "", "technisch": technik,
    })
    if not alt:
        e.update({"ls": None, "anlagen": [{"id": speicher.neue_id(), "name": n} for n in anlagen_vorschlag(technik)],
                  "anlagenPruefen": True, "zusatz": [],
                  "team": {"vor": _person_id("Matthias"), "fach": _person_id(zweiter)},
                  "geraete": {"fahrzeug": "f2", "kompressor": "k1", "strahl": ["s1"]},
                  "tage": [], "trash": [], "zeitenAufLs": False, "tagesUnterschrift": False})
    elif zweiter and alt.get("status") == "vorbereitet":
        e.setdefault("team", {})["fach"] = _person_id(zweiter) or e["team"].get("fach")
    return e, blocks


def uebernehmen(pid: str) -> tuple[dict, str]:
    """Holt ein Projekt und legt den Einsatz an bzw. aktualisiert ihn (solange er nicht läuft).
    Gibt (Einsatz, Hinweis) zurück."""
    p = seite(pid)
    alt = next((e for e in speicher.einsaetze() if (e.get("notion") or {}).get("id") == p["id"]), None)
    if alt and alt.get("status") != "vorbereitet":
        return alt, "Einsatz läuft bereits oder ist abgeschlossen – nicht überschrieben."
    e, blocks = als_einsatz(p, alt)
    hinweis = ""
    if not e.get("ls"):
        try:
            ls = lieferschein_datei(blocks)
        except NotionFehler:
            ls = None
        if ls:
            from .dokumente import lieferschein
            pdf = herunterladen(ls[1])
            speicher.pfad("dokumente", e["id"], "lexware.pdf").write_bytes(pdf)
            e["ls"] = lieferschein.lexware_nummer(pdf) or e.get("ls")
            hinweis = f"Lexware-Lieferschein {e['ls'] or ''} übernommen."
        else:
            hinweis = "Lieferschein liegt noch nicht unter Projektinfos → Unterlagen → Lieferschein."
    speicher.einsatz_speichern(e)
    speicher.protokoll("notion_uebernommen", "notion", e["id"], notion=p["id"])
    return e, hinweis
