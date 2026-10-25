"""Mailversand über das Postfach bei udmedia (SMTP).

Zugang steht in daten/geheim.json unter "mail":
  {"host": "smtp…", "port": 587, "benutzer": "lieferschein@lueke-strahlt.de",
   "passwort": "…", "absender": "LST Lüke-Strahl-Technik <lieferschein@lueke-strahlt.de>",
   "antwort_an": "post@lueke-strahlt.de", "bcc": "post@lueke-strahlt.de", "verbindung": "starttls"}

Ist noch nichts eingetragen oder schlägt der Versand fehl, landet die Mail im
Postausgang (Tabelle + .eml-Datei) und kann später erneut gesendet werden.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import speicher


def eingerichtet() -> bool:
    m = speicher.geheim().get("mail") or {}
    return bool(m.get("host") and m.get("benutzer") and m.get("passwort"))


def _nachricht(an: str, betreff: str, text: str, anhaenge: list[tuple[str, bytes]]) -> EmailMessage:
    m = speicher.geheim().get("mail") or {}
    msg = EmailMessage()
    msg["From"] = m.get("absender") or m.get("benutzer") or "LST Baustelle <noreply@localhost>"
    msg["To"] = an
    if m.get("antwort_an"):
        msg["Reply-To"] = m["antwort_an"]
    if m.get("bcc"):
        msg["Bcc"] = m["bcc"]
    msg["Subject"] = betreff
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="lueke-strahlt.de")
    msg.set_content(text)
    for name, daten in anhaenge:
        msg.add_attachment(daten, maintype="application", subtype="pdf", filename=name)
    return msg


def _abschicken(msg: EmailMessage):
    m = speicher.geheim()["mail"]
    port = int(m.get("port") or 587)
    ctx = ssl.create_default_context()
    if m.get("verbindung") == "ssl" or port == 465:
        with smtplib.SMTP_SSL(m["host"], port, context=ctx, timeout=30) as s:
            s.login(m["benutzer"], m["passwort"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(m["host"], port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(m["benutzer"], m["passwort"])
            s.send_message(msg)


def senden(an: str, betreff: str, text: str, anhaenge: list[tuple[str, bytes]], einsatz_id: str | None = None) -> dict:
    """Versendet sofort, wenn möglich. Ergebnis: {id, status: versendet|wartet|fehler, fehler}."""
    pid = speicher.neue_id()
    msg = _nachricht(an, betreff, text, anhaenge)
    eml = speicher.pfad("postausgang", f"{pid}.eml")
    eml.write_bytes(bytes(msg))
    status, fehler = "wartet", "Mailversand ist noch nicht eingerichtet."
    if eingerichtet():
        try:
            _abschicken(msg)
            status, fehler = "versendet", None
        except Exception as ex:  # Netz weg, Passwort falsch …
            status, fehler = "fehler", f"{type(ex).__name__}: {ex}"
    with speicher.db() as c:
        c.execute("INSERT INTO postausgang VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (pid, einsatz_id, an, betreff, text, json.dumps([n for n, _ in anhaenge], ensure_ascii=False),
                   status, fehler, time.time(), time.time() if status == "versendet" else None))
    return {"id": pid, "status": status, "fehler": fehler}


def erneut_senden(pid: str) -> dict:
    """Schickt eine wartende Mail aus dem Postausgang (z. B. nachdem das Netz wieder da ist)."""
    from email import message_from_bytes, policy
    eml = speicher.pfad("postausgang", f"{pid}.eml")
    msg = message_from_bytes(eml.read_bytes(), policy=policy.default)
    if not eingerichtet():
        return {"id": pid, "status": "wartet", "fehler": "Mailversand ist noch nicht eingerichtet."}
    try:
        _abschicken(msg)
        status, fehler = "versendet", None
    except Exception as ex:
        status, fehler = "fehler", f"{type(ex).__name__}: {ex}"
    with speicher.db() as c:
        c.execute("UPDATE postausgang SET status=?, fehler=?, gesendet=? WHERE id=?",
                  (status, fehler, time.time() if status == "versendet" else None, pid))
    return {"id": pid, "status": status, "fehler": fehler}


def postausgang(nur_offen=False) -> list[dict]:
    with speicher.db() as c:
        sql = "SELECT * FROM postausgang" + (" WHERE status!='versendet'" if nur_offen else "") + " ORDER BY erstellt DESC LIMIT 50"
        return [dict(r) for r in c.execute(sql).fetchall()]
