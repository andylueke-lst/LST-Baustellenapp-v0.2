"""Web-Server der Baustellen-App (Flask).

Start:  python3 -m lst.server            (http://localhost:8080)
Auf dem Mac mini später über Tailscale erreichbar, siehe README.
"""
from __future__ import annotations

import base64
import functools
import io
import json
import os
from datetime import date, timedelta

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import mail, notion, speicher
from .dokumente import abschluss as dok_abschluss
from .dokumente import basis, fotos, lieferschein

app = Flask(__name__, template_folder="vorlagen", static_folder="statisch")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
app.permanent_session_lifetime = timedelta(days=60)   # iPads bleiben angemeldet
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ---------------------------------------------------------------- Anmeldung
def ich() -> dict | None:
    uid = session.get("uid")
    return next((m for m in speicher.stamm("mitarbeiter", []) if m["id"] == uid), None)


def voller_name(m: dict | None) -> str:
    return dok_abschluss.voller_name(m) if m else ""


def anmeldung(admin=False):
    def deko(f):
        @functools.wraps(f)
        def innen(*a, **k):
            m = ich()
            if not m:
                if request.path.startswith("/api/"):
                    return jsonify(fehler="Bitte anmelden."), 401
                return redirect(url_for("login", weiter=request.path))
            if admin and m.get("rolle") != "admin":
                abort(403)
            return f(*a, **k)
        return innen
    return deko


@app.context_processor
def vorlagen_helfer():
    return {"ich": ich(), "datum": basis.datum, "zeitraum": basis.zeitraum, "voller_name": voller_name,
            "mail_eingerichtet": mail.eingerichtet()}


@app.route("/login", methods=["GET", "POST"])
def login():
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)]
    fehler = ""
    if request.method == "POST":
        m = next((x for x in leute if x["id"] == request.form.get("wer")), None)
        if m and not m.get("pin_hash"):
            fehler = f"Für {m['vorname']} ist noch keine PIN vergeben. Das geht im Büro unter „Mitarbeiter“."
        elif m and check_password_hash(m["pin_hash"], request.form.get("pin", "")):
            session.clear()
            session.permanent = True
            session["uid"] = m["id"]
            speicher.protokoll("anmeldung", m["id"])
            ziel = request.args.get("weiter") or (url_for("buero") if m.get("rolle") == "admin" else "/app")
            return redirect(ziel if ziel.startswith("/") else "/")
        else:
            fehler = "Name oder PIN stimmt nicht."
    return render_template("login.html", leute=leute, fehler=fehler)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- Büro
@app.route("/")
def start():
    m = ich()
    return redirect(url_for("buero") if m and m.get("rolle") == "admin" else "/app")


def _fotos_von(eid: str) -> list[dict]:
    with speicher.db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM fotos WHERE einsatz_id=? AND geloescht=0 ORDER BY erstellt", (eid,)).fetchall()]


def _offene_freigaben() -> list[dict]:
    aus = []
    for e in speicher.einsaetze("abgeschlossen"):
        docs = speicher.dokumente(e["id"])
        if not any(d["art"] == "fotobericht" and d["status"] in ("versendet", "wartet") for d in docs):
            aus.append({"e": e, "fotos": len(_fotos_von(e["id"]))})
    return aus


@app.route("/buero")
@anmeldung(admin=True)
def buero():
    alle = speicher.einsaetze()
    return render_template("buero.html", freigaben=_offene_freigaben(),
                           laufend=[e for e in alle if e.get("status") == "inarbeit"],
                           geplant=sorted([e for e in alle if e.get("status") == "vorbereitet"], key=lambda e: e.get("von", "")),
                           fertig=[e for e in alle if e.get("status") == "abgeschlossen"][:15],
                           post=mail.postausgang(nur_offen=True))


def _einsatz_oder_404(eid: str) -> dict:
    e = speicher.einsatz(eid)
    if not e:
        abort(404)
    return e


@app.route("/buero/einsatz/<eid>")
@anmeldung(admin=True)
def einsatz_seite(eid):
    e = _einsatz_oder_404(eid)
    docs = speicher.dokumente(eid)
    lex = speicher.pfad("dokumente", eid, "lexware.pdf")
    return render_template("einsatz.html", e=e, docs=docs, fotos=_fotos_von(eid), lexware=lex.exists(),
                           post=[p for p in mail.postausgang() if p["einsatz_id"] == eid])


# ---------------------------------------------------------------- Büro: Mitarbeiter und Einsätze anlegen
@app.route("/buero/mitarbeiter", methods=["GET", "POST"])
@anmeldung(admin=True)
def mitarbeiter_seite():
    leute = speicher.stamm("mitarbeiter", [])
    if request.method == "POST":
        f = request.form
        fehler = []
        for m in leute:
            i = m["id"]
            m["vorname"] = f.get(f"vorname_{i}", m["vorname"]).strip() or m["vorname"]
            m["nachname"] = f.get(f"nachname_{i}", "").strip()
            m["rolle"] = f.get(f"rolle_{i}", m.get("rolle"))
            m["team"] = f.get(f"team_{i}", m.get("team"))
            m["mail"] = f.get(f"mail_{i}", "").strip()
            m["aktiv"] = bool(f.get(f"aktiv_{i}"))
            pin = f.get(f"pin_{i}", "").strip()
            if pin:
                if pin.isdigit() and 4 <= len(pin) <= 6:
                    m["pin_hash"] = generate_password_hash(pin)
                    speicher.protokoll("pin_gesetzt", session.get("uid"), mitarbeiter=i)
                else:
                    fehler.append(m["vorname"])
        if f.get("vorname_neu", "").strip():
            ids = {m["id"] for m in leute}
            n = 1
            while f"m{n}" in ids:
                n += 1
            neu = {"id": f"m{n}", "vorname": f["vorname_neu"].strip(), "nachname": f.get("nachname_neu", "").strip(),
                   "rolle": f.get("rolle_neu", "mitarbeiter"), "team": f.get("team_neu", "Facharbeiter"),
                   "mail": f.get("mail_neu", "").strip(), "aktiv": True}
            pin = f.get("pin_neu", "").strip()
            if pin.isdigit() and 4 <= len(pin) <= 6:
                neu["pin_hash"] = generate_password_hash(pin)
            leute.append(neu)
        if not any(m.get("rolle") == "admin" and m.get("aktiv") and m.get("pin_hash") for m in leute):
            flash("Mindestens ein aktiver Admin mit PIN muss bleiben. Nichts gespeichert.", "fehler")
            return redirect(url_for("mitarbeiter_seite"))
        speicher.stamm_setzen("mitarbeiter", leute)
        flash("Gespeichert." + (f" PIN nicht geändert bei {', '.join(fehler)}: bitte 4 bis 6 Ziffern." if fehler else ""),
              "hinweis" if fehler else "ok")
        return redirect(url_for("mitarbeiter_seite"))
    return render_template("mitarbeiter.html", leute=leute)


def _leerer_einsatz() -> dict:
    return {"id": "", "art": "kunde", "status": "vorbereitet", "einsatzort": "", "kunde": "", "adresse": "",
            "apOrt": {"name": "", "tel": "", "mail": ""}, "apKunde": {"name": "", "mail": ""}, "von": "", "bis": "",
            "ls": None, "reinigungsart": "Wärmetauscher", "technisch": "", "anlagen": [], "zusatz": [],
            "team": {"vor": None, "fach": None}, "geraete": {"fahrzeug": "f2", "kompressor": "k1", "strahl": ["s1"]},
            "tage": [], "trash": [], "zeitenAufLs": False, "tagesUnterschrift": False}


def _zeilen(text: str, alt: list[dict]) -> list[dict]:
    """Eine Zeile je Anlage; bestehende behalten ihre id, damit Fotos dranbleiben."""
    namen = [z.strip() for z in (text or "").splitlines() if z.strip()]
    frei = list(alt)
    aus = []
    for n in namen:
        treffer = next((a for a in frei if a["name"] == n), None)
        if treffer:
            frei.remove(treffer)
            aus.append(treffer)
        else:
            aus.append({"id": speicher.neue_id(), "name": n})
    return aus


@app.route("/buero/einsatz/neu", methods=["GET", "POST"])
@app.route("/buero/einsatz/<eid>/bearbeiten", methods=["GET", "POST"])
@anmeldung(admin=True)
def einsatz_neu(eid=None):
    e = _einsatz_oder_404(eid) if eid else _leerer_einsatz()
    if e.get("status") == "abgeschlossen":
        flash("Ein abgeschlossener Einsatz lässt sich nicht mehr bearbeiten.", "hinweis")
        return redirect(url_for("einsatz_seite", eid=eid))
    if request.method == "POST":
        f = request.form
        if not e["id"]:
            e["id"] = speicher.neue_id()
        e.update({"einsatzort": f["einsatzort"].strip(), "kunde": f["kunde"].strip(), "adresse": f.get("adresse", "").strip(),
                  "apOrt": {"name": f.get("ort_name", "").strip(), "tel": f.get("ort_tel", "").strip(),
                            "mail": f.get("ort_mail", "").strip()},
                  "apKunde": {"name": f.get("k_name", "").strip(), "mail": f.get("k_mail", "").strip()},
                  "von": f["von"], "bis": max(f["von"], f["bis"]), "reinigungsart": f.get("reinigungsart", "").strip(),
                  "ls": f.get("ls", "").strip().upper() or e.get("ls"), "technisch": f.get("technisch", "").strip(),
                  "anlagen": _zeilen(f.get("anlagen"), e.get("anlagen", [])),
                  "zusatz": _zeilen(f.get("zusatz"), e.get("zusatz", [])),
                  "team": {"vor": f.get("vor") or None, "fach": f.get("fach") or None},
                  "zeitenAufLs": bool(f.get("zeitenAufLs")), "tagesUnterschrift": bool(f.get("tagesUnterschrift"))})
        e["geraete"] = {**e.get("geraete", {}), "fahrzeug": f.get("fahrzeug"), "kompressor": f.get("kompressor")}
        e.pop("anlagenPruefen", None)
        if not e["anlagen"]:
            flash("Bitte mindestens eine Anlage eintragen.", "fehler")
            return render_template("einsatz_form.html", e=e, **_auswahl())
        speicher.einsatz_speichern(e)
        speicher.protokoll("einsatz_gespeichert", session.get("uid"), e["id"])
        flash(f"Einsatz {e['einsatzort']} gespeichert. Er erscheint auf dem iPad unter „Vorbereitet“.", "ok")
        return redirect(url_for("einsatz_seite", eid=e["id"]))
    return render_template("einsatz_form.html", e=e, **_auswahl())


def _auswahl():
    return {"leute": [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)],
            "fahrzeuge": speicher.stamm("fahrzeuge", []),
            "kompressoren": [k for k in speicher.stamm("kompressoren", []) if k.get("aktiv", True)]}


@app.route("/buero/einsatz/<eid>/loeschen", methods=["POST"])
@anmeldung(admin=True)
def einsatz_loeschen(eid):
    e = _einsatz_oder_404(eid)
    if e.get("status") != "vorbereitet":
        flash("Nur vorbereitete Einsätze lassen sich löschen.", "fehler")
        return redirect(url_for("einsatz_seite", eid=eid))
    with speicher.db() as c:
        c.execute("DELETE FROM einsaetze WHERE id=?", (eid,))
    speicher.protokoll("einsatz_geloescht", session.get("uid"), eid, einsatzort=e.get("einsatzort"))
    flash(f"Einsatz {e['einsatzort']} gelöscht.", "ok")
    return redirect(url_for("buero"))


@app.route("/buero/notion")
@anmeldung(admin=True)
def notion_seite():
    liste, fehler = [], ""
    if notion.eingerichtet():
        try:
            vorhanden = {(e.get("notion") or {}).get("id"): e for e in speicher.einsaetze()}
            for p in notion.projekte_abfragen():
                k = notion.kurzinfo(p)
                k["einsatz"] = vorhanden.get(p["id"])
                liste.append(k)
        except notion.NotionFehler as ex:
            fehler = str(ex)
    return render_template("notion.html", liste=liste, fehler=fehler, eingerichtet=notion.eingerichtet(),
                           schreiben=bool(notion.einstellungen().get("schreiben")))


@app.route("/buero/notion/<pid>/uebernehmen", methods=["POST"])
@anmeldung(admin=True)
def notion_uebernehmen(pid):
    try:
        e, hinweis = notion.uebernehmen(pid)
    except notion.NotionFehler as ex:
        flash(str(ex), "fehler")
        return redirect(url_for("notion_seite"))
    flash(f"{e['einsatzort']} übernommen. {hinweis} Bitte Anlagen prüfen.", "ok")
    return redirect(url_for("einsatz_seite", eid=e["id"]))


@app.route("/buero/einsatz/<eid>/fotobericht")
@anmeldung(admin=True)
def fotobericht_seite(eid):
    e = _einsatz_oder_404(eid)
    fs = _fotos_von(eid)
    gruppen = []
    for a in e.get("anlagen", []):
        g = {"anlage": a, "vorher": [f for f in fs if f["anlage_id"] == a["id"] and f["phase"] == "vorher"],
             "nachher": [f for f in fs if f["anlage_id"] == a["id"] and f["phase"] == "nachher"]}
        if g["vorher"] or g["nachher"]:
            gruppen.append(g)
    tage = e.get("tage") or []
    zr = basis.zeitraum(tage[0]["datum"], tage[-1]["datum"]) if tage else basis.zeitraum(e["von"], e["bis"])
    text = (f"Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie den Fotobericht zu unserer Reinigung "
            f"am Einsatzort {e['einsatzort']} ({zr}).\n\nFür Rückfragen sind wir gerne für Sie da.\n\n"
            f"Mit freundlichen Grüßen\n{basis.config()['firma']}")
    return render_template("fotobericht.html", e=e, gruppen=gruppen, mailtext=text,
                           an=(e.get("apKunde") or {}).get("mail", ""),
                           betreff=f"Fotobericht {e['einsatzort']} {zr}",
                           kb_pro_foto=260, ziel_mb=basis.config()["fotos"]["bericht_ziel_mb"])


def _quelle(eid):
    return lambda fid: speicher.foto_datei(eid, fid)


def _einsatz_mit_fotos(e: dict) -> dict:
    """Ergänzt den Einsatz um die Fotos aus der Datenbank (Form wie im Prototyp)."""
    e = dict(e)
    e["fotos"] = [{"id": f["id"], "anlage": f["anlage_id"], "phase": f["phase"], "tag": f["tag"] or 0,
                   "ts": int(f["erstellt"] * 1000), "up": 1} for f in _fotos_von(e["id"])]
    return e


@app.route("/api/einsatz/<eid>/fotobericht/vorschau", methods=["POST"])
@anmeldung(admin=True)
def fotobericht_vorschau(eid):
    e = _einsatz_mit_fotos(_einsatz_oder_404(eid))
    auswahl = json.loads(request.form.get("fotos") or "[]")
    if not auswahl:
        return "Bitte mindestens ein Foto auswählen.", 400
    try:
        name, pdf, info = dok_abschluss.fotobericht_aus_auswahl(e, auswahl, _quelle(eid), heute=_doku_datum(e))
    except fotos.ZuGross as ex:
        return str(ex), 400
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name=name)


@app.route("/api/einsatz/<eid>/fotobericht/freigeben", methods=["POST"])
@anmeldung(admin=True)
def fotobericht_freigeben(eid):
    e = _einsatz_mit_fotos(_einsatz_oder_404(eid))
    d = request.get_json(force=True)
    auswahl, an = d.get("fotos") or [], (d.get("an") or "").strip()
    if not auswahl:
        return jsonify(fehler="Bitte mindestens ein Foto auswählen."), 400
    if "@" not in an or "." not in an.split("@")[-1]:
        return jsonify(fehler="Die Empfänger-Adresse sieht unvollständig aus."), 400
    try:
        name, pdf, info = dok_abschluss.fotobericht_aus_auswahl(e, auswahl, _quelle(eid), heute=_doku_datum(e))
    except fotos.ZuGross as ex:
        return jsonify(fehler=str(ex)), 400
    ergebnis = mail.senden(an, d.get("betreff") or name[:-4], d.get("text") or "", [(name, pdf)], eid)
    status = "versendet" if ergebnis["status"] == "versendet" else "wartet"
    speicher.dokument_speichern(eid, "fotobericht", "FB" + basis.nummer_ziffern(e.get("ls")), name, pdf, status,
                                basis.pruefsumme(pdf), an, {**info, "fotos": auswahl, "mail": ergebnis["id"]})
    speicher.protokoll("fotobericht_freigegeben", session.get("uid"), eid, an=an, fotos=len(auswahl),
                       mail=ergebnis["status"])
    flash(f"Fotobericht mit {len(auswahl)} Fotos ({info['mb']:.1f} MB) an {an} versendet."
          if ergebnis["status"] == "versendet" else
          f"Fotobericht erstellt und im Postausgang abgelegt: {ergebnis['fehler']}",
          "ok" if ergebnis["status"] == "versendet" else "hinweis")
    return jsonify(ok=True, status=ergebnis["status"], fehler=ergebnis["fehler"], datei=name, mb=info["mb"])


@app.route("/buero/einsatz/<eid>/lexware", methods=["POST"])
@anmeldung(admin=True)
def lexware_hochladen(eid):
    e = _einsatz_oder_404(eid)
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        flash("Bitte eine PDF-Datei auswählen.", "fehler")
        return redirect(url_for("einsatz_seite", eid=eid))
    daten = f.read()
    speicher.pfad("dokumente", eid, "lexware.pdf").write_bytes(daten)
    nr = lieferschein.lexware_nummer(daten)
    if nr:
        e["ls"] = nr
        speicher.einsatz_speichern(e)
    flash(f"Lexware-Lieferschein {nr or ''} gespeichert.".replace("  ", " "), "ok")
    return redirect(url_for("einsatz_seite", eid=eid))


@app.route("/buero/einsatz/<eid>/fotodoku-senden", methods=["POST"])
@anmeldung(admin=True)
def fotodoku_senden(eid):
    e = _einsatz_oder_404(eid)
    an = request.form.get("an", "").strip()
    fd = next((d for d in speicher.dokumente(eid) if d["art"] == "fotodoku"), None)
    if not fd or "@" not in an:
        flash("Fotodoku oder Empfänger fehlt.", "fehler")
        return redirect(url_for("einsatz_seite", eid=eid))
    datei = speicher.DATEN / fd["datei"]
    erg = mail.senden(an, f"Fotodokumentation {e['einsatzort']}", request.form.get("text", ""),
                      [(datei.name, datei.read_bytes())], eid)
    speicher.protokoll("fotodoku_gesendet", session.get("uid"), eid, an=an, mail=erg["status"])
    flash("Fotodoku versendet." if erg["status"] == "versendet" else f"Fotodoku liegt im Postausgang: {erg['fehler']}",
          "ok" if erg["status"] == "versendet" else "hinweis")
    return redirect(url_for("einsatz_seite", eid=eid))


@app.route("/buero/postausgang/<pid>/senden", methods=["POST"])
@anmeldung(admin=True)
def postausgang_senden(pid):
    erg = mail.erneut_senden(pid)
    flash("Mail versendet." if erg["status"] == "versendet" else f"Nicht versendet: {erg['fehler']}",
          "ok" if erg["status"] == "versendet" else "hinweis")
    return redirect(request.referrer or url_for("buero"))


@app.route("/dokument/<did>")
@anmeldung(admin=True)
def dokument(did):
    d = next((x for x in speicher.dokumente() if x["id"] == did), None)
    if not d:
        abort(404)
    return send_file(speicher.DATEN / d["datei"], mimetype="application/pdf")


@app.route("/foto/<eid>/<fid>")
@anmeldung()
def foto(eid, fid):
    p = speicher.foto_datei(eid, fid, vorschau=request.args.get("v") == "1")
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/jpeg", max_age=86400)


# ---------------------------------------------------------------- App fürs iPad (Oberfläche)
APP = speicher.ROOT / "lst" / "app"


@app.route("/app")
@app.route("/app/")
def app_seite():
    r = send_file(APP / "index.html", mimetype="text/html", max_age=0)
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.route("/app/<datei>")
def app_datei(datei):
    erlaubt = {"sw.js": "text/javascript", "manifest.webmanifest": "application/manifest+json",
               "icon-180.png": "image/png", "icon-512.png": "image/png"}
    if datei not in erlaubt:
        abort(404)
    r = send_file(APP / datei, mimetype=erlaubt[datei], max_age=0)
    if datei == "sw.js":
        r.headers["Service-Worker-Allowed"] = "/"
        r.headers["Cache-Control"] = "no-cache"
    return r


# ---------------------------------------------------------------- Schnittstelle für iPad und Handy
@app.route("/api/einsatz", methods=["POST"])
@anmeldung()
def api_einsatz_speichern():
    e = request.get_json(force=True)
    if not e.get("id"):
        return jsonify(fehler="id fehlt"), 400
    alt = speicher.einsatz(e["id"]) or {}
    liste = e.pop("fotos", None)
    for k in ("trash", "lsHtml", "sync_abschluss", "serverDocs"):
        e.pop(k, None)
    # Abschluss und Lexware-Nummer bestimmt der Server, nicht das iPad
    if alt.get("status") == "abgeschlossen":
        e["status"], e["abschluss"] = "abgeschlossen", alt.get("abschluss")
    elif e.get("status") == "abgeschlossen":
        e["status"] = "inarbeit"
        e.pop("abschluss", None)
    if alt.get("ls") and not e.get("ls"):
        e["ls"] = alt["ls"]
    speicher.einsatz_speichern(e)
    if liste is not None:
        speicher.fotos_abgleichen(e["id"], liste)
    return jsonify(ok=True)


# ---------------------------------------------------------------- iPad: Anmeldung und Gesamtstand
def _fuer_app(m: dict) -> dict:
    return {"id": m["id"], "name": m.get("vorname", ""), "nachname": m.get("nachname", ""),
            "rolle": m.get("rolle", "mitarbeiter"),
            "team": m.get("team") or ("Vorarbeiter" if m.get("rolle") == "technik" else "Facharbeiter")}


@app.route("/api/leute")
def api_leute():
    return jsonify([{"id": m["id"], "name": m.get("vorname", ""), "pin": bool(m.get("pin_hash"))}
                    for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)])


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(force=True)
    m = next((x for x in speicher.stamm("mitarbeiter", []) if x["id"] == d.get("wer") and x.get("aktiv", True)), None)
    if m and not m.get("pin_hash"):
        return jsonify(fehler=f"Für {m['vorname']} ist noch keine PIN vergeben."), 403
    if not m or not check_password_hash(m["pin_hash"], str(d.get("pin", ""))):
        speicher.protokoll("anmeldung_fehlgeschlagen", d.get("wer"))
        return jsonify(fehler="PIN stimmt nicht."), 403
    session.clear()
    session.permanent = True
    session["uid"] = m["id"]
    speicher.protokoll("anmeldung", m["id"], geraet="ipad")
    return jsonify(ok=True, ich=_fuer_app(m))


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify(ok=True)


LISTEN = ("maengel", "tanken", "reisetage", "fahrzeuge", "kompressoren", "intern", "notizen")


@app.route("/api/stand")
@anmeldung()
def api_stand():
    """Alles, was das iPad zum Arbeiten braucht. Abgeschlossene Einsätze nur der letzten 30 Tage."""
    grenze = (date.today() - timedelta(days=30)).isoformat()
    es = []
    for e in speicher.einsaetze():
        if e.get("status") == "abgeschlossen" and (e.get("abschluss") or {}).get("datum", "9999") < grenze:
            continue
        es.append(_einsatz_mit_fotos(e))
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)]
    aus = {"ich": _fuer_app(ich()), "mitarbeiter": [_fuer_app(m) for m in leute], "einsaetze": es,
           "strahlgeraete": speicher.stamm("strahlgeraete", []), "typen": speicher.stamm("geraetetypen", {}),
           "strahlmittel": speicher.stamm("strahlmittel", {}), "checkliste": speicher.stamm("checkliste", [])}
    for k in LISTEN:
        aus[k] = speicher.stamm(k, [])
    return jsonify(aus)


@app.route("/api/listen", methods=["POST"])
@anmeldung()
def api_listen():
    """Mängel, Tankvorgänge, Reisetage, Fahrzeuge, Kompressoren und interne Aufträge vom iPad.
    Zusammengeführt nach id, damit zwei iPads sich nicht gegenseitig überschreiben."""
    d = request.get_json(force=True)
    for k in LISTEN:
        if not isinstance(d.get(k), list):
            continue
        alt = {x["id"]: x for x in speicher.stamm(k, []) if isinstance(x, dict) and x.get("id")}
        for x in d[k]:
            if isinstance(x, dict) and x.get("id"):
                alt[x["id"]] = {**alt.get(x["id"], {}), **x}
        for wid in (d.get("geloescht") or {}).get(k, []):
            alt.pop(wid, None)
        speicher.stamm_setzen(k, list(alt.values()))
    return jsonify(ok=True)


@app.route("/api/einsatz/<eid>/foto", methods=["POST"])
@anmeldung()
def api_foto(eid):
    _einsatz_oder_404(eid)
    f = request.files.get("foto")
    if not f:
        return jsonify(fehler="foto fehlt"), 400
    fid = speicher.foto_speichern(eid, f.read(), request.form.get("anlage", ""), request.form.get("phase", "vorher"),
                                  int(request.form.get("tag", 0)), request.form.get("id") or None)
    return jsonify(ok=True, id=fid)


def _abschluss_datum(wert):
    """Tag der Unterschrift bzw. des Abschlusses. Das iPad schickt ihn mit, damit ein
    später nachgereichter Abschluss (offline) trotzdem das richtige Datum bekommt."""
    try:
        return date.fromisoformat(wert).isoformat() if wert else date.today().isoformat()
    except (TypeError, ValueError):
        return date.today().isoformat()


def _doku_datum(e):
    """Datum für Dateinamen nachträglicher Dokumente: Tag des Job-Abschlusses."""
    return (e.get("abschluss") or {}).get("datum") or date.today().isoformat()


@app.route("/api/einsatz/<eid>/abschluss", methods=["POST"])
@anmeldung()
def api_abschluss(eid):
    """Job abschließen: Dokumente erzeugen, Lieferschein ggf. an den Einsatzort senden,
    Fotobericht in die Freigabe legen, Admins benachrichtigen."""
    e = _einsatz_mit_fotos(_einsatz_oder_404(eid))
    sig = request.get_json(force=True)
    if sig.get("bild"):
        sig["bild"] = base64.b64decode(sig["bild"].split(",")[-1])
    lex = speicher.pfad("dokumente", eid, "lexware.pdf")
    lex_pdf = lex.read_bytes() if lex.exists() else None
    stamm = {"mitarbeiter": {m["id"]: m for m in speicher.stamm("mitarbeiter", [])},
             "strahlgeraete": {g["id"]: g for g in speicher.stamm("strahlgeraete", [])},
             "typen": speicher.stamm("geraetetypen", {})}
    datum = _abschluss_datum(sig.get("datum"))
    docs = dok_abschluss.einsatz_dokumente(e, stamm, _quelle(eid), lex_pdf, sig, heute=datum)
    fd_name, fd_pdf, fd_info = docs["fotodoku"]
    ls_name, ls_pdf, ls_info = docs["lieferschein"]
    nr = basis.nummer_ziffern(e.get("ls"))
    speicher.dokument_speichern(eid, "fotodoku", "FD" + nr, fd_name, fd_pdf, "erstellt", fd_info["pruefsumme"])
    ls_status = "unterschrift_ausstehend" if sig.get("ohne_kunde") else "unterschrieben"
    did = speicher.dokument_speichern(eid, "lieferschein", e.get("ls") or "", ls_name, ls_pdf, ls_status,
                                      basis.pruefsumme(ls_pdf), info=ls_info)
    versand = None
    if sig.get("versand") and sig.get("mail") and not sig.get("spaeter"):
        versand = mail.senden(sig["mail"], f"Lieferschein {e.get('ls') or ''} {e['einsatzort']}".strip(),
                              "Guten Tag,\n\nanbei der unterschriebene Lieferschein.\n\nMit freundlichen Grüßen\n"
                              + basis.config()["firma"], [(ls_name, ls_pdf)], eid)
        speicher.dokument_status(did, ls_status, sig["mail"])
    e = speicher.einsatz(eid)
    e["status"] = "abgeschlossen"
    e["abschluss"] = {"datum": datum, "name": sig.get("name"), "ohneKunde": bool(sig.get("ohne_kunde"))}
    speicher.einsatz_speichern(e)
    admins = [m for m in speicher.stamm("mitarbeiter", []) if m.get("rolle") == "admin" and m.get("mail")]
    for a in admins:
        mail.senden(a["mail"], f"Fotobericht zur Freigabe: {e['einsatzort']}",
                    f"Der Job {e['einsatzort']} ist abgeschlossen. Der Fotobericht wartet auf deine Freigabe.", [], eid)
    speicher.protokoll("job_abgeschlossen", session.get("uid"), eid, ohne_kunde=bool(sig.get("ohne_kunde")))
    return jsonify(ok=True, lieferschein=ls_name, fotodoku=fd_name, versand=versand)


def main():
    speicher.einrichten()
    app.secret_key = speicher.geheim()["sitzung"]
    port = int(os.environ.get("LST_PORT", 8080))
    host = os.environ.get("LST_HOST", "127.0.0.1")
    if os.environ.get("LST_DEBUG"):
        app.run(host=host, port=port, debug=True)
        return
    try:
        from waitress import serve   # Server für den Dauerbetrieb auf dem Mac mini
    except ImportError:
        app.run(host=host, port=port, threaded=True)
    else:
        print(f"LST Baustelle läuft auf http://{host}:{port}", flush=True)
        serve(app, host=host, port=port, threads=8, max_request_body_size=app.config["MAX_CONTENT_LENGTH"])


speicher.einrichten()
app.secret_key = speicher.geheim()["sitzung"]

if __name__ == "__main__":
    main()
