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
from datetime import date

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.security import check_password_hash

from . import mail, speicher
from .dokumente import abschluss as dok_abschluss
from .dokumente import basis, fotos, lieferschein

app = Flask(__name__, template_folder="vorlagen", static_folder="statisch")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024


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
        if m and m.get("pin_hash") and check_password_hash(m["pin_hash"], request.form.get("pin", "")):
            session.clear()
            session["uid"] = m["id"]
            speicher.protokoll("anmeldung", m["id"])
            return redirect(request.args.get("weiter") or url_for("buero"))
        fehler = "Name oder PIN stimmt nicht."
    return render_template("login.html", leute=leute, fehler=fehler)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- Büro
@app.route("/")
def start():
    return redirect(url_for("buero"))


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
    e["fotos"] = [{"id": f["id"], "anlage": f["anlage_id"], "phase": f["phase"]} for f in _fotos_von(e["id"])]
    return e


@app.route("/api/einsatz/<eid>/fotobericht/vorschau", methods=["POST"])
@anmeldung(admin=True)
def fotobericht_vorschau(eid):
    e = _einsatz_mit_fotos(_einsatz_oder_404(eid))
    auswahl = json.loads(request.form.get("fotos") or "[]")
    if not auswahl:
        return "Bitte mindestens ein Foto auswählen.", 400
    try:
        name, pdf, info = dok_abschluss.fotobericht_aus_auswahl(e, auswahl, _quelle(eid))
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
        name, pdf, info = dok_abschluss.fotobericht_aus_auswahl(e, auswahl, _quelle(eid))
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


# ---------------------------------------------------------------- Schnittstelle für iPad und Handy
@app.route("/api/einsatz", methods=["POST"])
@anmeldung()
def api_einsatz_speichern():
    e = request.get_json(force=True)
    if not e.get("id"):
        return jsonify(fehler="id fehlt"), 400
    e.pop("fotos", None)  # Fotos laufen über eigene Anfragen
    speicher.einsatz_speichern(e)
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
    docs = dok_abschluss.einsatz_dokumente(e, stamm, _quelle(eid), lex_pdf, sig)
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
    e["abschluss"] = {"datum": date.today().isoformat(), "name": sig.get("name"), "ohneKunde": bool(sig.get("ohne_kunde"))}
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
    app.run(host=os.environ.get("LST_HOST", "127.0.0.1"), port=port, debug=bool(os.environ.get("LST_DEBUG")))


speicher.einrichten()
app.secret_key = speicher.geheim()["sitzung"]

if __name__ == "__main__":
    main()
