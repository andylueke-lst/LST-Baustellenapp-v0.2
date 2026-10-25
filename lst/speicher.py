"""Datenhaltung: eine SQLite-Datei plus Ordner für Fotos und Dokumente.

daten/
  lst.db                 Einsätze, Fotos, Dokumente, Postausgang, Stammdaten
  fotos/<einsatz>/       Originale und Vorschaubilder
  dokumente/<einsatz>/   erzeugte PDFs (Lieferschein, Fotodoku, Fotobericht …)
  postausgang/           Mails, die (noch) nicht versendet werden konnten (.eml)
  geheim.json            Sitzungsschlüssel und Mail-Zugang, nie in Git
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
DATEN = Path(os.environ.get("LST_DATEN", ROOT / "daten"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS einsaetze (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, json TEXT NOT NULL, geaendert REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fotos (
  id TEXT PRIMARY KEY, einsatz_id TEXT NOT NULL, anlage_id TEXT, phase TEXT, tag INTEGER,
  datei TEXT NOT NULL, erstellt REAL NOT NULL, geloescht INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dokumente (
  id TEXT PRIMARY KEY, einsatz_id TEXT NOT NULL, art TEXT NOT NULL, nummer TEXT, datei TEXT NOT NULL,
  pruefsumme TEXT, status TEXT NOT NULL, empfaenger TEXT, info TEXT, erstellt REAL NOT NULL, gesendet REAL);
CREATE TABLE IF NOT EXISTS postausgang (
  id TEXT PRIMARY KEY, einsatz_id TEXT, an TEXT, betreff TEXT, text TEXT, anhaenge TEXT,
  status TEXT NOT NULL, fehler TEXT, erstellt REAL NOT NULL, gesendet REAL);
CREATE TABLE IF NOT EXISTS stammdaten (schluessel TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS protokoll (
  zeit REAL NOT NULL, wer TEXT, was TEXT NOT NULL, einsatz_id TEXT, details TEXT);
"""


def pfad(*teile) -> Path:
    p = DATEN.joinpath(*teile)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def db():
    DATEN.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATEN / "lst.db")
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def einrichten():
    with db() as c:
        c.executescript(SCHEMA)


def geheim() -> dict:
    """Sitzungsschlüssel wird beim ersten Start erzeugt. Mail-Zugang trägt Andy direkt
    in daten/geheim.json ein (siehe README)."""
    p = pfad("geheim.json")
    if not p.exists():
        p.write_text(json.dumps({"sitzung": secrets.token_hex(32), "mail": {}}, indent=2))
        os.chmod(p, 0o600)
    return json.loads(p.read_text())


def neue_id() -> str:
    return uuid.uuid4().hex[:12]


def protokoll(was: str, wer: str | None = None, einsatz_id: str | None = None, **details):
    with db() as c:
        c.execute("INSERT INTO protokoll VALUES (?,?,?,?,?)",
                  (time.time(), wer, was, einsatz_id, json.dumps(details, ensure_ascii=False)))


# ---------------------------------------------------------------- Stammdaten
def stamm(schluessel: str, standard=None):
    with db() as c:
        r = c.execute("SELECT json FROM stammdaten WHERE schluessel=?", (schluessel,)).fetchone()
    return json.loads(r["json"]) if r else standard


def stamm_setzen(schluessel: str, wert):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO stammdaten VALUES (?,?)", (schluessel, json.dumps(wert, ensure_ascii=False)))


# ---------------------------------------------------------------- Einsätze
def einsatz(eid: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT json FROM einsaetze WHERE id=?", (eid,)).fetchone()
    return json.loads(r["json"]) if r else None


def einsatz_speichern(e: dict):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO einsaetze VALUES (?,?,?,?)",
                  (e["id"], e.get("status", "vorbereitet"), json.dumps(e, ensure_ascii=False), time.time()))


def einsaetze(status: str | None = None) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT json FROM einsaetze" + (" WHERE status=?" if status else "") + " ORDER BY geaendert DESC",
                         (status,) if status else ()).fetchall()
    return [json.loads(r["json"]) for r in rows]


# ---------------------------------------------------------------- Fotos
def foto_speichern(einsatz_id: str, daten: bytes, anlage_id: str, phase: str, tag: int = 0,
                   foto_id: str | None = None) -> str:
    """Speichert das Original und ein Vorschaubild (400 px)."""
    fid = foto_id or neue_id()
    original = pfad("fotos", einsatz_id, f"{fid}.jpg")
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(daten))).convert("RGB")
    im.save(original, "JPEG", quality=92)
    vorschau = im.copy()
    vorschau.thumbnail((400, 400))
    vorschau.save(pfad("fotos", einsatz_id, f"{fid}_v.jpg"), "JPEG", quality=78)
    with db() as c:
        c.execute("INSERT OR REPLACE INTO fotos (id,einsatz_id,anlage_id,phase,tag,datei,erstellt) VALUES (?,?,?,?,?,?,?)",
                  (fid, einsatz_id, anlage_id, phase, tag, str(original.relative_to(DATEN)), time.time()))
    return fid


def foto_datei(einsatz_id: str, fid: str, vorschau=False) -> Path:
    return DATEN / "fotos" / einsatz_id / (f"{fid}_v.jpg" if vorschau else f"{fid}.jpg")


# ---------------------------------------------------------------- Dokumente
def dokument_speichern(einsatz_id: str, art: str, nummer: str, dateiname: str, pdf: bytes, status: str,
                       pruefsumme: str | None = None, empfaenger: str = "", info: dict | None = None) -> str:
    did = neue_id()
    ziel = pfad("dokumente", einsatz_id, dateiname)
    ziel.write_bytes(pdf)
    with db() as c:
        c.execute("INSERT INTO dokumente VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (did, einsatz_id, art, nummer, str(ziel.relative_to(DATEN)), pruefsumme, status, empfaenger,
                   json.dumps(info or {}, ensure_ascii=False), time.time(), None))
    return did


def dokumente(einsatz_id: str | None = None, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM dokumente WHERE 1=1", []
    if einsatz_id:
        sql += " AND einsatz_id=?"
        args.append(einsatz_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    with db() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY erstellt DESC", args).fetchall()]


def dokument_status(did: str, status: str, empfaenger: str | None = None):
    with db() as c:
        c.execute("UPDATE dokumente SET status=?, empfaenger=COALESCE(?, empfaenger), gesendet=CASE WHEN ?='versendet' "
                  "THEN ? ELSE gesendet END WHERE id=?", (status, empfaenger, status, time.time(), did))
