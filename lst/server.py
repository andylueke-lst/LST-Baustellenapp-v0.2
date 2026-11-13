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
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import altdaten, mail, notion, speicher
from .dokumente import abschluss as dok_abschluss
from .dokumente import basis, fotos, lieferschein

app = Flask(__name__, template_folder="vorlagen", static_folder="statisch")
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024   # Übernahme aus der alten App: viele Fotos auf einmal
app.permanent_session_lifetime = timedelta(days=60)   # iPads bleiben angemeldet
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ---------------------------------------------------------------- Anmeldung
def ich() -> dict | None:
    uid = session.get("uid")
    return next((m for m in speicher.stamm("mitarbeiter", []) if m["id"] == uid), None)


def voller_name(m: dict | None) -> str:
    return dok_abschluss.voller_name(m) if m else ""


def darf_einsatz(e: dict | None, m: dict | None) -> bool:
    """Wer einen Einsatz auf dem iPad sieht und bearbeiten darf: Admins (Andy, Tina) alle,
    alle anderen nur Einsätze, in denen sie im Team stehen oder an einem Tag Zeiten haben."""
    if not e or not m:
        return False
    if m.get("rolle") == "admin":
        return True
    uid = m["id"]
    team = e.get("team") or {}
    if uid in (team.get("vor"), team.get("fach")) or uid in (team.get("weitere") or []):
        return True
    return any(uid in (t.get("zeiten") or {}) for t in e.get("tage") or [])


def _einsatz_fuer_mich(eid: str) -> dict:
    """Einsatz laden, aber nur, wenn er dem Angemeldeten zugeteilt ist (sonst 404 wie nicht vorhanden)."""
    e = speicher.einsatz(eid)
    if not darf_einsatz(e, ich()):
        abort(404)
    return e


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


def jobname(e: dict | None) -> str:
    """Anzeigename eines Jobs: Projektname aus Notion (Kunde Einsatzort Tätigkeit Jahr), sonst Einsatzort."""
    e = e or {}
    return e.get("name") or (e.get("notion") or {}).get("projektname") or e.get("einsatzort") or e.get("kunde") or ""


@app.context_processor
def vorlagen_helfer():
    return {"ich": ich(), "jobname": jobname, "datum": basis.datum, "zeitraum": basis.zeitraum, "voller_name": voller_name,
            "mail_eingerichtet": mail.eingerichtet(),
            "zeitpunkt": lambda ts: time.strftime("%d.%m.%Y, %H:%M Uhr", time.localtime(ts)) if ts else ""}


def empfaenger_liste(*teile: str) -> list[str]:
    """Adressen aus Feldern wie „a@x.de; b@y.de“ – ohne Doppelte, in Reihenfolge."""
    aus = []
    for t in teile:
        for a in re.split(r"[,;\s]+", t or ""):
            a = a.strip()
            if a and a.lower() not in (x.lower() for x in aus):
                aus.append(a)
    return aus


def adresse_ok(a: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a))


@app.route("/login", methods=["GET", "POST"])
def login():
    # Büro/Browser: alle Aktiven mit PIN (der Haken „Anmeldung am iPad“ gilt nur für die iPad-App)
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)
             and (m.get("pin_hash") or m.get("rolle") == "admin")]
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
    namen = {m["id"]: m.get("vorname", "") for m in speicher.stamm("mitarbeiter", [])}
    kachel = lambda e: _kachel(e, namen)
    fertig = sorted((e for e in alle if e.get("status") == "abgeschlossen"),
                    key=lambda e: (e.get("abschluss") or {}).get("datum") or e.get("bis") or "", reverse=True)
    aufgaben = _aufgaben_liste(namen)
    return render_template("buero.html", freigaben=_offene_freigaben(),
                           laufend=[kachel(e) for e in alle if e.get("status") == "inarbeit"],
                           geplant=[kachel(e) for e in sorted((e for e in alle if e.get("status") == "vorbereitet"),
                                                               key=lambda e: e.get("von", ""))],
                           fertig=[kachel(e) for e in fertig[:12]], fertig_mehr=max(0, len(fertig) - 12),
                           aufgaben=[a for a in aufgaben if a["status"] != "erledigt"][:8],
                           aufgaben_offen=sum(1 for a in aufgaben if a["status"] != "erledigt"),
                           post=mail.postausgang(nur_offen=True), heute=date.today().isoformat())


def _kachel(e: dict, namen: dict) -> dict:
    """Was eine Einsatz-Kachel auf der Startseite zeigt: Team, heutiger Stand, Hinweise."""
    from .auswertung import berechnen
    team = [x for x in [(e.get("team") or {}).get("vor"), (e.get("team") or {}).get("fach")]
            + list((e.get("team") or {}).get("weitere") or []) if x]
    for t in e.get("tage") or []:
        team += [mid for mid in (t.get("zeiten") or {}) if mid not in team]
    heute = date.today().isoformat()
    jetzt = []
    for t in e.get("tage") or []:
        if t.get("datum") != heute:
            continue
        for mid, z in (t.get("zeiten") or {}).items():
            p = (z or {}).get("p") or {}
            if p.get("4") and not p.get("5"):
                stand = f"unterwegs seit {p['4']}"
            elif p.get("3") and not p.get("4"):
                stand = f"vor Ort seit {p['3']}"
            elif p.get("2") and not p.get("3"):
                stand = f"fährt seit {p['2']}"
            elif p.get("5") or p.get("6"):
                c = berechnen(z)
                stand = "Feierabend" + (f" ({c['gesamt'] // 60}:{c['gesamt'] % 60:02d} h)" if c["gesamt"] else "")
            else:
                stand = "noch keine Zeit"
            jetzt.append(f"{namen.get(mid, mid)}: {stand}")
    ab = e.get("abschluss") or {}
    return {"e": e, "team": [namen.get(m, m) for m in team], "jetzt": jetzt,
            "tage": len(e.get("tage") or []), "anlagen": len(e.get("anlagen") or []),
            "ohne_unterschrift": bool(ab.get("ohneKunde")) if e.get("status") == "abgeschlossen" else False}


def _aufgaben_liste(namen: dict) -> list[dict]:
    """Alle internen Aufgaben (aus Notion und auf dem iPad angelegte), offene nach Termin."""
    heute = date.today().isoformat()
    aus = []
    for i in speicher.stamm("intern", []):
        if not isinstance(i, dict):
            continue
        wer = ([i["zust"]] if i.get("zust") else list(i.get("fuer") or []))
        zeiten = list((i.get("zeiten") or {}).values()) or ([i["zeit"]] if i.get("zeit") else [])
        minuten = 0
        for z in zeiten:
            try:
                a, b = (int(x[:2]) * 60 + int(x[3:5]) for x in (z["von"], z["bis"]))
                minuten += (b - a) % 1440
            except (KeyError, TypeError, ValueError):
                pass
        aus.append({**i, "wer": [namen.get(m, m) for m in wer] or ["alle"], "minuten": minuten,
                    "ueberfaellig": i.get("status") != "erledigt" and bool(i.get("datum")) and i["datum"] < heute,
                    "heute": i.get("datum") == heute})
    aus.sort(key=lambda a: (a.get("status") == "erledigt", not a.get("datum"), a.get("datum") or "", a.get("rang") or 9))
    return aus


@app.route("/buero/aufgaben")
@anmeldung(admin=True)
def aufgaben_seite():
    namen = {m["id"]: m.get("vorname", "") for m in speicher.stamm("mitarbeiter", [])}
    alle = _aufgaben_liste(namen)
    wer = [w for w in request.args.getlist("wer") if w]
    if wer:
        alle = [a for a in alle if (a.get("zust") in wer) or any(f in wer for f in (a.get("fuer") or []))
                or (not a.get("zust") and not a.get("fuer"))]
    erledigt = sorted((a for a in alle if a["status"] == "erledigt"), key=lambda a: a.get("erledigt_am") or "", reverse=True)
    offen = [a for a in alle if a["status"] != "erledigt"]
    gruppen = [("Überfällig", [a for a in offen if a["ueberfaellig"]]),
               ("Heute", [a for a in offen if a["heute"]]),
               ("Demnächst", [a for a in offen if a.get("datum") and not a["ueberfaellig"] and not a["heute"]]),
               ("Ohne Termin", [a for a in offen if not a.get("datum")])]
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True) and m.get("ipad", True)]
    return render_template("aufgaben.html", gruppen=gruppen, erledigt=erledigt[:30], leute=leute, wer=wer,
                           stand=AUFGABEN_STAND, eingerichtet=notion.eingerichtet())


@app.route("/buero/postausgang/<pid>/loeschen", methods=["POST"])
@anmeldung(admin=True)
def postausgang_loeschen(pid):
    with speicher.db() as c:
        c.execute("DELETE FROM postausgang WHERE id=?", (pid,))
    try:
        speicher.pfad("postausgang", f"{pid}.eml").unlink()
    except OSError:
        pass
    speicher.protokoll("postausgang_geloescht", session.get("uid"), mail=pid)
    flash("Mail aus dem Postausgang gelöscht.", "ok")
    return redirect(request.referrer or url_for("buero"))


def _einsatz_oder_404(eid: str) -> dict:
    e = speicher.einsatz(eid)
    if not e:
        abort(404)
    return e


@app.route("/buero/einsatz/<eid>")
@anmeldung(admin=True)
def einsatz_seite(eid):
    e = _einsatz_oder_404(eid)
    docs = speicher.dokumente(eid)   # neueste zuerst
    aktuell, gesehen = set(), set()
    for d in docs:
        if d["art"] not in gesehen:
            gesehen.add(d["art"])
            aktuell.add(d["id"])
    lex = speicher.pfad("dokumente", eid, "lexware.pdf")
    return render_template("einsatz.html", e=e, docs=docs, aktuell=aktuell, fotos=_fotos_von(eid), lexware=lex.exists(),
                           uf=_unterschrift_felder(e.get("unterschrift")),
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
            m["ipad"] = bool(f.get(f"ipad_{i}"))
            m["minijob"] = bool(f.get(f"minijob_{i}"))
            m["baustelle"] = bool(f.get(f"baustelle_{i}"))
            m["funktion"] = (f.get(f"funktion_{i}") or "").strip()
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
                   "mail": f.get("mail_neu", "").strip(), "aktiv": True, "ipad": bool(f.get("ipad_neu")),
                   "minijob": bool(f.get("minijob_neu")), "baustelle": bool(f.get("baustelle_neu")),
                   "funktion": (f.get("funktion_neu") or "").strip()}
            pin = f.get("pin_neu", "").strip()
            if pin.isdigit() and 4 <= len(pin) <= 6:
                neu["pin_hash"] = generate_password_hash(pin)
            leute.append(neu)
        if not any(m.get("rolle") == "admin" and m.get("aktiv") and m.get("ipad", True) and m.get("pin_hash") for m in leute):
            flash("Mindestens ein aktiver Admin mit PIN muss bleiben. Nichts gespeichert.", "fehler")
            return redirect(url_for("mitarbeiter_seite"))
        speicher.stamm_setzen("mitarbeiter", leute)
        flash("Gespeichert." + (f" PIN nicht geändert bei {', '.join(fehler)}: bitte 4 bis 6 Ziffern." if fehler else ""),
              "hinweis" if fehler else "ok")
        return redirect(url_for("mitarbeiter_seite"))
    return render_template("mitarbeiter.html", leute=leute)


def _leerer_einsatz() -> dict:
    return {"id": "", "art": "kunde", "status": "vorbereitet", "einsatzort": "", "kunde": "", "adresse": "",
            "apOrt": {"name": "", "tel": "", "mail": ""}, "apKunde": {"name": "", "mail": "", "tel": ""}, "von": "", "bis": "",
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
        e.update({"name": f.get("name", "").strip(), "einsatzort": f["einsatzort"].strip(), "kunde": f["kunde"].strip(), "adresse": f.get("adresse", "").strip(),
                  "apOrt": {"name": f.get("ort_name", "").strip(), "tel": f.get("ort_tel", "").strip(),
                            "mail": f.get("ort_mail", "").strip()},
                  "apKunde": {"name": f.get("k_name", "").strip(), "mail": f.get("k_mail", "").strip(),
                              "tel": f.get("k_tel", "").strip()},
                  "von": f["von"], "bis": max(f["von"], f["bis"]), "reinigungsart": f.get("reinigungsart", "").strip(),
                  "ls": f.get("ls", "").strip().upper() or e.get("ls"), "technisch": f.get("technisch", "").strip(),
                  "anlagen": _zeilen(f.get("anlagen"), e.get("anlagen", [])),
                  "zusatz": _zeilen(f.get("zusatz"), e.get("zusatz", [])),
                  "team": {"vor": f.get("vor") or None, "fach": f.get("fach") or None,
                           "weitere": [x for x in f.getlist("weitere") if x and x not in (f.get("vor"), f.get("fach"))]},
                  "team_manuell": True,
                  "zeitenAufLs": bool(f.get("zeitenAufLs")), "tagesUnterschrift": bool(f.get("tagesUnterschrift"))})
        e["geraete"] = {**e.get("geraete", {}), "fahrzeug": f.get("fahrzeug"), "kompressor": f.get("kompressor") or None,
                       "strahl": f.getlist("strahl")[:2]}
        e.pop("anlagenPruefen", None)
        if not e["anlagen"]:
            flash("Bitte mindestens eine Anlage eintragen.", "fehler")
            return render_template("einsatz_form.html", e=e, **_auswahl())
        speicher.einsatz_speichern(e)
        speicher.protokoll("einsatz_gespeichert", session.get("uid"), e["id"])
        flash(f"Einsatz {jobname(e)} gespeichert. Er erscheint auf dem iPad unter „Vorbereitet“.", "ok")
        return redirect(url_for("einsatz_seite", eid=e["id"]))
    return render_template("einsatz_form.html", e=e, **_auswahl())


def _auswahl():
    return {"leute": [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True) and m.get("baustelle", True)],
            "fahrzeuge": [f for f in speicher.stamm("fahrzeuge", []) if f.get("aktiv", True)],
            "kompressoren": [k for k in speicher.stamm("kompressoren", []) if k.get("aktiv", True)],
            "strahlgeraete": [g for g in speicher.stamm("strahlgeraete", []) if g.get("aktiv", True)]}


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
    liste, ausgeblendet, fehler, reisen = [], [], "", []
    if notion.eingerichtet():
        try:
            alle_e = speicher.einsaetze()
            vorhanden = {}
            for e in alle_e:
                for nid in [(e.get("notion") or {}).get("id")] + list(e.get("notion_weitere") or []):
                    if nid:
                        vorhanden[nid] = e
            weg = set(speicher.stamm("notion_ausgeblendet", []))
            vorhanden = {str(k).replace("-", ""): v for k, v in vorhanden.items()}
            alle_k = []
            for p in notion.projekte_abfragen():
                k = notion.kurzinfo(p)
                k["einsatz"] = vorhanden.get(p["id"].replace("-", ""))
                if k["einsatz"] and not k["einsatz"].get("name") and \
                        str((k["einsatz"].get("notion") or {}).get("id", "")).replace("-", "") == p["id"].replace("-", ""):
                    k["einsatz"]["name"] = k["name"]           # ältere Einsätze: Projektname als Jobname
                    speicher.einsatz_speichern(k["einsatz"])
                alle_k.append(k)
            # Zusammengehörige Projekte: Notion-Feld „Projektverknüpfung“ (in beide Richtungen)
            namen = {k["id"].replace("-", ""): k["name"] for k in alle_k}
            for k in alle_k:
                kid = k["id"].replace("-", "")
                partner = set(k["verknuepft"]) | {x["id"].replace("-", "") for x in alle_k if kid in x["verknuepft"]}
                k["partner"] = [namen.get(x) or (jobname(vorhanden[x]) if x in vorhanden else "weiteres Projekt") for x in partner]
                k["partner_ids"] = partner
                if not k["einsatz"]:
                    # „Gehört zu“ nur, wenn der Einsatz eines verknüpften Projekts am selben Tag/Zeitraum liegt;
                    # sonst ist es eine weitere Station (Tour) und wird eigener Einsatz
                    k["kandidaten"] = [e for e in {id(e): e for e in (vorhanden.get(x) for x in partner) if e}.values()
                                       if _ueberschneidet(k, e)]
                (ausgeblendet if p_ausgeblendet(k, weg) else liste).append(k)
            liste = _gruppieren(liste)
            reisen = _reisetage_uebersicht(alle_e, vorhanden, alle_k)
            for g in liste:          # Reisetage zur Tour: verknüpft oder 1–2 Tage vor Beginn / nach Ende
                if g["typ"] != "tour":
                    continue
                ids = {p["id"].replace("-", "") for p in g["ps"]}
                vb, nb = date.fromisoformat(g["von"]), date.fromisoformat(g["bis"])
                g["reisen"] = []
                for r in reisen:
                    if r["angelegt"]:
                        continue
                    rd = date.fromisoformat(r["von"])
                    verkn = bool(ids & (set(r["verknuepft"]) | ({k["id"].replace("-", "") for k in g["ps"] if r["id"].replace("-", "") in k["verknuepft"]})))
                    if verkn or timedelta(days=1) <= vb - rd <= timedelta(days=2) or timedelta(days=1) <= rd - nb <= timedelta(days=2):
                        g["reisen"].append({**r, "verkn": verkn, "art": "Anreise" if rd < vb else "Abreise"})
        except notion.NotionFehler as ex:
            fehler = str(ex)
    alle = speicher.stamm("intern", [])
    intern = [i for i in alle if str(i.get("id", "")).startswith("n-")]
    grenze = (date.today() - timedelta(days=14)).isoformat()
    leute = {m["id"]: m.get("vorname", "") for m in speicher.stamm("mitarbeiter", [])}
    grenze_z = (date.today() - timedelta(days=60)).isoformat()
    zuordenbar = sorted((e for e in speicher.einsaetze() if (e.get("bis") or e.get("von") or "") >= grenze_z),
                        key=lambda e: e.get("von") or "", reverse=True)
    return render_template("notion.html", liste=liste, ausgeblendet=ausgeblendet, fehler=fehler, eingerichtet=notion.eingerichtet(),
                           zuordenbar=zuordenbar, reisen=reisen, tour_leute=_auswahl()["leute"], fahrzeuge=_auswahl()["fahrzeuge"],
                           kompressoren=_auswahl()["kompressoren"],
                           schreiben=bool(notion.einstellungen().get("schreiben")),
                           aufgaben_offen=[i for i in intern if i.get("status") != "erledigt"],
                           aufgaben_erledigt=[i for i in alle if i.get("status") == "erledigt" and (
                               str(i.get("id", "")).startswith("n-") or (i.get("erledigt_am") or "") >= grenze)],
                           aufgaben_stand=AUFGABEN_STAND, leute=leute)


AUFGABEN_STAND = {"zeit": None, "ergebnis": None, "fehler": ""}


def aufgaben_holen():
    """Interne Aufgaben aus Notion holen (alle 5 Minuten im Hintergrund und per Knopf im Büro)."""
    if not notion.eingerichtet():
        return
    try:
        AUFGABEN_STAND["ergebnis"] = notion.aufgaben_abgleichen()
        AUFGABEN_STAND["fehler"] = ""
    except notion.NotionFehler as ex:
        AUFGABEN_STAND["fehler"] = str(ex)
    AUFGABEN_STAND["zeit"] = time.strftime("%H:%M")


def _aufgaben_takt():
    runde = 0
    while True:
        try:
            aufgaben_holen()
        except Exception as ex:   # der Takt darf nie stehen bleiben
            AUFGABEN_STAND["fehler"] = f"Unerwarteter Fehler: {ex}"
        if runde % 12 == 0 and notion.eingerichtet():   # Kontakte stündlich
            try:
                notion.kontakte_abgleichen()
            except Exception:
                pass
        runde += 1
        time.sleep(300)


# ---------------------------------------------------------------- Interne Aufträge: Fotos und Arbeitsbericht
def _intern(iid: str) -> dict | None:
    return next((i for i in speicher.stamm("intern", []) if i.get("id") == iid), None)


def _darf_intern(iid: str) -> bool:
    m = ich()
    if not m:
        return False
    if m.get("rolle") == "admin":
        return True
    i = _intern(iid)
    if i is None:          # gerade auf dem iPad angelegt, noch nicht abgeglichen
        return True
    return intern_fuer(i, m["id"]) or i.get("erledigt_von") == m["id"]


def intern_fuer(i: dict, mid: str) -> bool:
    """Interner Auftrag sichtbar für mid: zugeordnet, für mehrere inkl. mid offen, oder für alle offen."""
    if i.get("zust"):
        return i["zust"] == mid
    return not i.get("fuer") or mid in i["fuer"]


@app.route("/api/intern/<iid>/foto", methods=["POST"])
@anmeldung()
def api_intern_foto(iid):
    if not _darf_intern(iid):
        return jsonify(fehler="Dieser Auftrag ist dir nicht zugeteilt."), 403
    f = request.files.get("foto")
    if not f:
        return jsonify(fehler="foto fehlt"), 400
    phase = request.form.get("phase") if request.form.get("phase") in ("vorher", "nachher") else "vorher"
    fid = speicher.foto_speichern("intern-" + iid, f.read(), "", phase)
    return jsonify(ok=True, id=fid)


@app.route("/intern-foto/<iid>/<fid>")
@anmeldung()
def intern_foto(iid, fid):
    if not _darf_intern(iid):
        abort(404)
    p = speicher.foto_datei("intern-" + iid, fid, vorschau=request.args.get("v") == "1")
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="image/jpeg", max_age=86400)


@app.route("/buero/intern/<iid>/bericht")
@anmeldung(admin=True)
def intern_bericht(iid):
    i = _intern(iid)
    if not i:
        abort(404)
    from .dokumente import intern as dok_intern
    z = i.get("zeit") or {}
    wer = next((m for m in speicher.stamm("mitarbeiter", []) if m["id"] == (i.get("erledigt_von") or i.get("zust"))), None)
    datum = z.get("datum") or i.get("erledigt_am") or date.today().isoformat()
    fotos = [{"phase": f.get("phase"), "quelle": speicher.foto_datei("intern-" + iid, f["id"])}
             for f in i.get("fotos") or [] if speicher.foto_datei("intern-" + iid, f["id"]).exists()]
    pdf = dok_intern.arbeitsbericht({"titel": i.get("titel", ""), "projekt": i.get("projekt", ""),
                                     "beschreibung": i.get("beschreibung", ""), "name": voller_name(wer),
                                     "datum": datum, "von": z.get("von"), "bis": z.get("bis"),
                                     "text": i.get("notiz", ""), "material": i.get("material", ""), "fotos": fotos,
                                     "zeiten": [{"name": voller_name(next((m for m in speicher.stamm("mitarbeiter", [])
                                                                          if m["id"] == mid), None)) or mid, **(zz or {})}
                                                for mid, zz in (i.get("zeiten") or {}).items()]})
    name = basis.dateiname(datum, "LST", "intern", "Arbeitsbericht", i.get("titel", "")[:40])
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name=name)


@app.route("/buero/notion/aufgaben", methods=["POST"])
@anmeldung(admin=True)
def notion_aufgaben():
    aufgaben_holen()
    try:
        notion.kontakte_abgleichen()
    except notion.NotionFehler:
        pass
    if AUFGABEN_STAND["fehler"]:
        flash(AUFGABEN_STAND["fehler"], "fehler")
    elif AUFGABEN_STAND["ergebnis"]:
        r = AUFGABEN_STAND["ergebnis"]
        flash(f"{r['anzahl']} Aufgaben für die iPads geholt." + (f" {r['unbekannt']} mit unbekanntem Namen bei „iPad für“ übersprungen." if r["unbekannt"] else ""), "ok")
    return redirect(request.referrer or url_for("notion_seite"))


def _reisetage_uebersicht(alle_e: list[dict], vorhanden: dict, projekte: list[dict]) -> list[dict]:
    """Reisetage aus Notion mit Ziel-Einsatz: über „Projektverknüpfung“ (sicher) oder als Vorschlag
    der Einsatz, der am Tag danach beginnt bzw. am Tag davor endet."""
    da = {r.get("notion_id", "").replace("-", ""): r for r in speicher.stamm("reisetage", []) if r.get("notion_id")}
    proj_ids = {k["id"].replace("-", ""): k for k in projekte}
    aus = []
    for rt in notion.reisetage_abfragen():
        rid = rt["id"].replace("-", "")
        partner = set(rt["verknuepft"]) | {k["id"].replace("-", "") for k in projekte if rid in k.get("verknuepft", [])}
        ziel = next((vorhanden[x] for x in partner if x in vorhanden), None)
        wartet = None if ziel else next((proj_ids[x]["name"] for x in partner if x in proj_ids), None)
        vorschlag = None
        if not ziel and not partner:
            try:
                d = date.fromisoformat(rt["von"])
                for e in sorted(alle_e, key=lambda e: e.get("von") or ""):
                    ev, eb = date.fromisoformat(e.get("von", "")), date.fromisoformat(e.get("bis") or e.get("von", ""))
                    if timedelta(days=0) < ev - d <= timedelta(days=2) or timedelta(days=0) < d - eb <= timedelta(days=2):
                        vorschlag = e
                        break
            except ValueError:
                pass
        r = da.get(rid)
        aus.append({**rt, "ziel": ziel, "wartet": wartet, "vorschlag": vorschlag, "angelegt": r,
                    "einsatz_von_angelegt": next((e for e in alle_e if r and e["id"] == r.get("einsatz")), None)})
    return aus


def _ueberschneidet(k: dict, e: dict) -> bool:
    try:
        kv, kb = date.fromisoformat(k["von"]), date.fromisoformat(k["bis"] or k["von"])
        ev, eb = date.fromisoformat(e.get("von", "")), date.fromisoformat(e.get("bis") or e.get("von", ""))
    except (KeyError, ValueError):
        return False
    return ev <= kb and eb >= kv      # echte Überschneidung; Tourstationen an Folgetagen sind eigene Jobs


def _gruppieren(liste: list[dict]) -> list[dict]:
    """Verknüpfte Projekte (Notion „Projektverknüpfung“) zu Gruppen zusammenfassen, z. B. eine Tour.
    Ergebnis: [{"typ": "einzel", "p": …} | {"typ": "tour", "ps": […], "id": …}] in zeitlicher Reihenfolge."""
    nach_id = {k["id"].replace("-", ""): k for k in liste}
    gesehen, aus = set(), []
    for k in liste:
        kid = k["id"].replace("-", "")
        if kid in gesehen:
            continue
        gruppe, stapel = [], [kid]
        while stapel:
            x = stapel.pop()
            if x in gesehen or x not in nach_id:
                continue
            gesehen.add(x)
            gruppe.append(nach_id[x])
            stapel += list(nach_id[x].get("partner_ids") or [])
        if len(gruppe) > 1:
            gruppe.sort(key=lambda g: (g["von"], g["name"]))
            aus.append({"typ": "tour", "ps": gruppe, "id": "-".join(sorted(g["id"].replace("-", "")[:8] for g in gruppe)),
                        "von": gruppe[0]["von"], "bis": max(g["bis"] or g["von"] for g in gruppe),
                        "offen": [g for g in gruppe if not g["einsatz"] and not g.get("kandidaten")]})
        else:
            aus.append({"typ": "einzel", "p": k})
    return aus


def p_ausgeblendet(k: dict, weg: set) -> bool:
    return k["id"] in weg


def _reisetag_art(datum: str, e: dict) -> str:
    if datum < (e.get("von") or datum):
        return "anreise"
    if datum > (e.get("bis") or e.get("von") or datum):
        return "abreise"
    return "wechsel"


def _reisetag_anlegen(rt: dict, e: dict) -> dict:
    """Geplanten Reisetag aus Notion an einen Einsatz hängen (Zeiten trägt das iPad nach)."""
    liste = speicher.stamm("reisetage", [])
    rid = "nr-" + rt["id"].replace("-", "")[:16]
    if rid in set(speicher.stamm("geloescht_im_buero", {}).get("reisetage", [])):
        weg = speicher.stamm("geloescht_im_buero", {})
        weg["reisetage"] = [x for x in weg.get("reisetage", []) if x != rid]
        speicher.stamm_setzen("geloescht_im_buero", weg)
    team = e.get("team") or {}
    ma = [x for x in [team.get("vor")] + [notion._person_id(n) for n in rt.get("zweiter") or []] if x]
    if len(ma) < 2 and team.get("fach") and team["fach"] not in ma:
        ma.append(team["fach"])
    alt = next((r for r in liste if r.get("id") == rid), None)
    r = {**(alt or {}), "id": rid, "einsatz": e["id"], "art": _reisetag_art(rt["von"], e), "datum": rt["von"],
         "ma": (alt or {}).get("ma") or list(dict.fromkeys(ma)), "ab": (alt or {}).get("ab"), "an": (alt or {}).get("an"),
         "notiz": (alt or {}).get("notiz") or rt["name"], "geplant": True, "notion_id": rt["id"]}
    liste = [x for x in liste if x.get("id") != rid] + [r]
    speicher.stamm_setzen("reisetage", liste)
    return r


def _reisetage_zu(einsaetze: list[dict]):
    """Nach dem Übernehmen: in Notion verknüpfte Reisetage gleich mit anlegen."""
    try:
        rts = notion.reisetage_abfragen()
    except notion.NotionFehler:
        return 0
    n = 0
    for e in einsaetze:
        nids = {str(x).replace("-", "") for x in [(e.get("notion") or {}).get("id")] + list(e.get("notion_weitere") or []) if x}
        for rt in rts:
            if set(rt["verknuepft"]) & nids or any(nid in rt["verknuepft"] for nid in nids):
                _reisetag_anlegen(rt, e)
                n += 1
    return n


@app.route("/buero/notion/reisetag/<pid>", methods=["POST"])
@anmeldung(admin=True)
def notion_reisetag(pid):
    e = _einsatz_oder_404(request.form.get("eid", ""))
    rt = next((r for r in notion.reisetage_abfragen() if r["id"] == pid), None)
    if not rt:
        flash("Reisetag nicht gefunden.", "fehler")
        return redirect(url_for("notion_seite"))
    r = _reisetag_anlegen(rt, e)
    arten = {"anreise": "Anreise", "abreise": "Abreise", "wechsel": "Reise zwischen Jobs"}
    flash(f"{arten[r['art']]} am {basis.datum(r['datum'])} an „{jobname(e)}“ gehängt. Die Zeiten trägt das iPad ein.", "ok")
    return redirect(url_for("notion_seite"))


@app.route("/buero/notion/tour", methods=["POST"])
@anmeldung(admin=True)
def notion_tour():
    """Alle Stationen einer Tour übernehmen. Team und Geräte gelten für alle, wenn gewählt
    („wie in Notion“ lässt die Angaben aus Notion stehen); jede Station bleibt einzeln änderbar."""
    f = request.form
    pids = [x for x in f.getlist("pid") if x]
    tour_id = f.get("tour") or speicher.neue_id()
    angelegt, fehler = [], []
    for pid in pids:
        try:
            e, _ = notion.uebernehmen(pid)
        except notion.NotionFehler as ex:
            fehler.append(str(ex))
            continue
        angelegt.append(e)
    neu_ids = {e["id"] for e in angelegt}
    # Nummerierung über die ganze Tour, auch Stationen, die schon vorher übernommen waren
    alle_pids = {x.replace("-", "") for x in f.getlist("alle") if x} | {x.replace("-", "") for x in pids}
    stationen = {e["id"]: e for e in angelegt}
    for e in speicher.einsaetze():
        if str((e.get("notion") or {}).get("id", "")).replace("-", "") in alle_pids:
            stationen.setdefault(e["id"], e)
    reihe = sorted(stationen.values(), key=lambda e: (e.get("von") or "", jobname(e)))
    for n, e in enumerate(reihe, 1):
        e["tour"] = {"id": tour_id, "nr": n, "anzahl": len(reihe)}
        if e["id"] not in neu_ids:
            speicher.einsatz_speichern(e)
            continue
        if e.get("status") == "vorbereitet":
            team = dict(e.get("team") or {})
            for k in ("vor", "fach"):
                if f.get(k) == "-":
                    team[k] = None
                elif f.get(k):
                    team[k] = f[k]
            if f.getlist("weitere"):
                team["weitere"] = [x for x in f.getlist("weitere") if x not in (team.get("vor"), team.get("fach"))]
            if any(f.get(k) for k in ("vor", "fach")) or f.getlist("weitere"):
                e["team_manuell"] = True
            e["team"] = team
            g = dict(e.get("geraete") or {})
            if f.get("fahrzeug"):
                g["fahrzeug"] = f["fahrzeug"]
            if f.get("kompressor"):
                g["kompressor"] = None if f["kompressor"] == "-" else f["kompressor"]
            e["geraete"] = g
        speicher.einsatz_speichern(e)
    n_rt = _reisetage_zu(angelegt)
    gewaehlt = [x for x in f.getlist("reisetag") if x]
    if gewaehlt and reihe:
        for rt in notion.reisetage_abfragen():
            if rt["id"] in gewaehlt:
                ziel = reihe[0] if rt["von"] <= (reihe[0].get("von") or rt["von"]) else reihe[-1]
                _reisetag_anlegen(rt, ziel)
                n_rt += 1
    speicher.protokoll("notion_tour", session.get("uid"), None, stationen=len(angelegt), reisetage=n_rt)
    flash(f"{len(angelegt)} Stationen der Tour ({len(reihe)} insgesamt)" + (f" und {n_rt} Reisetag(e)" if n_rt else "") + " übernommen. Jede Station lässt sich einzeln unter „Bearbeiten“ anpassen."
          + (" Fehler: " + "; ".join(fehler) if fehler else ""), "fehler" if fehler else "ok")
    return redirect(url_for("notion_seite"))


@app.route("/buero/notion/<pid>/verknuepfen", methods=["POST"])
@anmeldung(admin=True)
def notion_verknuepfen(pid):
    """Notion-Projekt einem vorhandenen Einsatz zuordnen (z. B. nachträglich in Notion angelegt)."""
    e = _einsatz_oder_404(request.form.get("eid", ""))
    if (e.get("notion") or {}).get("id") and e["notion"]["id"] != pid:
        e["notion_weitere"] = sorted(set(e.get("notion_weitere") or []) | {pid})
    else:
        e["notion"] = {**(e.get("notion") or {}), "id": pid}
    speicher.einsatz_speichern(e)
    speicher.protokoll("notion_verknuepft", session.get("uid"), e["id"], notion=pid)
    flash(f"Notion-Projekt mit „{jobname(e)}“ verknüpft.", "ok")
    return redirect(url_for("notion_seite"))


@app.route("/buero/notion/<pid>/ausblenden", methods=["POST"])
@anmeldung(admin=True)
def notion_ausblenden(pid):
    weg = speicher.stamm("notion_ausgeblendet", [])
    if request.form.get("zeigen"):
        weg = [x for x in weg if x != pid]
    elif pid not in weg:
        weg.append(pid)
    speicher.stamm_setzen("notion_ausgeblendet", weg)
    return redirect(url_for("notion_seite"))


@app.route("/buero/notion/<pid>/uebernehmen", methods=["POST"])
@anmeldung(admin=True)
def notion_uebernehmen(pid):
    try:
        e, hinweis = notion.uebernehmen(pid)
    except notion.NotionFehler as ex:
        flash(str(ex), "fehler")
        return redirect(url_for("notion_seite"))
    n_rt = _reisetage_zu([e])
    flash(f"{jobname(e)} übernommen. {hinweis}" + (f" {n_rt} Reisetag(e) aus Notion dazu." if n_rt else "") + " Bitte Anlagen prüfen.", "ok")
    return redirect(url_for("einsatz_seite", eid=e["id"]))


def _fotos_geordnet(eid: str) -> list[dict]:
    """Fotos des Einsatzes mit der im Büro per Verschieben festgelegten Reihenfolge und Zuordnung
    (Anlage, vorher/nachher). Gilt nur für den Fotobericht; die Fotos auf dem iPad bleiben unverändert."""
    fs = _fotos_von(eid)
    ordnung = speicher.stamm(f"fotoordnung_{eid}", [])
    pos = {o["id"]: (n, o) for n, o in enumerate(ordnung) if isinstance(o, dict) and o.get("id")}
    for f in fs:
        if f["id"] in pos:
            o = pos[f["id"]][1]
            f["anlage_id"] = o.get("anlage") or f["anlage_id"]
            f["phase"] = o.get("phase") if o.get("phase") in ("vorher", "nachher") else f["phase"]
    return sorted(fs, key=lambda f: (pos[f["id"]][0] if f["id"] in pos else len(pos), f["erstellt"]))


@app.route("/api/einsatz/<eid>/fotobericht/ordnung", methods=["POST"])
@anmeldung(admin=True)
def fotobericht_ordnung(eid):
    _einsatz_oder_404(eid)
    liste = [{"id": str(o["id"]), "anlage": str(o.get("anlage") or ""), "phase": o.get("phase")}
             for o in (request.get_json(force=True) or {}).get("fotos", []) if isinstance(o, dict) and o.get("id")]
    speicher.stamm_setzen(f"fotoordnung_{eid}", liste)
    return jsonify(ok=True)


@app.route("/buero/einsatz/<eid>/fotos/hochladen", methods=["POST"])
@anmeldung(admin=True)
def fotos_hochladen(eid):
    """Weitere Fotos im Büro hinzufügen (Mediathek, Mac, aus Notion heruntergeladen)."""
    e = _einsatz_oder_404(eid)
    anlage = request.form.get("anlage") or ""
    phase = request.form.get("phase") if request.form.get("phase") in ("vorher", "nachher") else "nachher"
    if not any(a["id"] == anlage for a in e.get("anlagen", [])):
        return jsonify(fehler="Unbekannte Anlage."), 400
    neu, fehler = [], []
    for f in request.files.getlist("fotos"):
        try:
            neu.append(speicher.foto_speichern(eid, f.read(), anlage, phase))
        except Exception:
            fehler.append(f.filename or "Datei")
    if neu:
        ids = speicher.stamm(f"buerofotos_{eid}", [])
        speicher.stamm_setzen(f"buerofotos_{eid}", ids + neu)
        speicher.protokoll("fotos_im_buero", session.get("uid"), eid, anzahl=len(neu))
    return jsonify(ok=True, neu=[{"id": i, "url": url_for("foto", eid=eid, fid=i, v=1)} for i in neu],
                   fehler=("Nicht lesbar: " + ", ".join(fehler) + " (bitte als JPEG oder PNG)") if fehler else "")


@app.route("/buero/einsatz/<eid>/fotobericht/ordnung-zuruecksetzen", methods=["POST"])
@anmeldung(admin=True)
def fotobericht_ordnung_zuruecksetzen(eid):
    speicher.stamm_setzen(f"fotoordnung_{eid}", [])
    flash("Reihenfolge und Zuordnung wie vom iPad.", "ok")
    return redirect(url_for("fotobericht_seite", eid=eid))


@app.route("/buero/einsatz/<eid>/fotobericht")
@anmeldung(admin=True)
def fotobericht_seite(eid):
    e = _einsatz_oder_404(eid)
    fs = _fotos_geordnet(eid)
    gruppen = []
    for a in e.get("anlagen", []):
        g = {"anlage": a, "vorher": [f for f in fs if f["anlage_id"] == a["id"] and f["phase"] == "vorher"],
             "nachher": [f for f in fs if f["anlage_id"] == a["id"] and f["phase"] == "nachher"]}
        gruppen.append(g)   # auch Anlagen ohne Fotos: dorthin kann man ziehen oder hochladen
    tage = e.get("tage") or []
    zr = basis.zeitraum(tage[0]["datum"], tage[-1]["datum"]) if tage else basis.zeitraum(e["von"], e["bis"])
    text = (f"Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie den Fotobericht zu unserer Reinigung "
            f"am Einsatzort {e['einsatzort']} ({zr}).\n\nFür Rückfragen sind wir gerne für Sie da.\n\n"
            f"Mit freundlichen Grüßen\n{basis.config()['firma']}")
    return render_template("fotobericht.html", e=e, gruppen=gruppen, mailtext=text,
                           an=(e.get("apKunde") or {}).get("mail", ""),
                           betreff=f"Fotobericht {e['einsatzort']} {zr}",
                           kb_pro_foto=260, ziel_mb=basis.config()["fotos"]["bericht_ziel_mb"],
                           umgeordnet=bool(speicher.stamm(f"fotoordnung_{eid}", [])))


def _quelle(eid):
    return lambda fid: speicher.foto_datei(eid, fid)


def _einsatz_mit_fotos(e: dict, buero_ordnung: bool = False) -> dict:
    """Ergänzt den Einsatz um die Fotos aus der Datenbank (Form wie im Prototyp).
    buero_ordnung: Reihenfolge/Zuordnung aus dem Fotobericht-Editor anwenden."""
    e = dict(e)
    e["fotos"] = [{"id": f["id"], "anlage": f["anlage_id"], "phase": f["phase"], "tag": f["tag"] or 0,
                   "ts": int(f["erstellt"] * 1000), "up": 1}
                  for f in (_fotos_geordnet(e["id"]) if buero_ordnung else _fotos_von(e["id"]))]
    return e


@app.route("/api/einsatz/<eid>/fotobericht/vorschau", methods=["POST"])
@anmeldung(admin=True)
def fotobericht_vorschau(eid):
    e = _einsatz_mit_fotos(_einsatz_oder_404(eid), buero_ordnung=True)
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
    e = _einsatz_mit_fotos(_einsatz_oder_404(eid), buero_ordnung=True)
    d = request.get_json(force=True)
    auswahl = d.get("fotos") or []
    liste = empfaenger_liste(d.get("an"), d.get("weitere"))
    if not auswahl:
        return jsonify(fehler="Bitte mindestens ein Foto auswählen."), 400
    falsch = [a for a in liste if not adresse_ok(a)]
    if not liste or falsch:
        return jsonify(fehler="Diese Adresse sieht unvollständig aus: " + (falsch[0] if falsch else "(leer)")), 400
    an = ", ".join(liste)
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
    p = speicher.DATEN / d["datei"]
    return send_file(p, mimetype="application/pdf", download_name=p.name)


# Dokumente, die auch auf dem iPad geladen werden dürfen (nicht nur im Büro)
APP_DOKUMENTE = ("lieferschein", "fotodoku")


def _docs_fuer_app(eid: str) -> list[dict]:
    """Jeweils das neueste Dokument je Art, für die Teilen-Knöpfe auf dem iPad."""
    aus, gesehen = [], set()
    for d in speicher.dokumente(eid):   # neueste zuerst
        if d["art"] in APP_DOKUMENTE and d["art"] not in gesehen:
            gesehen.add(d["art"])
            aus.append({"id": d["id"], "art": d["art"], "name": Path(d["datei"]).name, "status": d["status"],
                        "seiten": _seitenzahl(speicher.DATEN / d["datei"])})
    return sorted(aus, key=lambda d: APP_DOKUMENTE.index(d["art"]))


def _seitenzahl(p: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(p)).pages)
    except Exception:
        return 0


@app.route("/api/dokument/<did>/seite/<int:nr>")
@anmeldung()
def api_dokument_seite(did, nr):
    """Eine Seite als Bild (für die Ansicht auf dem iPad, ohne PDF-Viewer). Wird zwischengespeichert."""
    d = next((x for x in speicher.dokumente() if x["id"] == did), None)
    if not d or d["art"] not in APP_DOKUMENTE or not darf_einsatz(speicher.einsatz(d["einsatz_id"]), ich()):
        abort(404)
    ziel = speicher.pfad("vorschau", did, f"{nr}.jpg")
    if not ziel.exists():
        import shutil
        import subprocess
        if not shutil.which("pdftoppm"):
            return jsonify(fehler="Seitenansicht fehlt auf dem Server (poppler-utils)."), 503
        r = subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=82", "-r", "110", "-f", str(nr), "-l", str(nr),
                            "-singlefile", str(speicher.DATEN / d["datei"]), str(ziel)[:-4]], capture_output=True, timeout=60)
        if r.returncode or not ziel.exists():
            abort(404)
    return send_file(ziel, mimetype="image/jpeg", max_age=3600)


@app.route("/api/dokument/<did>")
@anmeldung()
def api_dokument(did):
    """Lieferschein oder Fotodoku als PDF fürs iPad (Teilen, In Dateien sichern, AirDrop)."""
    d = next((x for x in speicher.dokumente() if x["id"] == did), None)
    if not d or d["art"] not in APP_DOKUMENTE or not darf_einsatz(speicher.einsatz(d["einsatz_id"]), ich()):
        return jsonify(fehler="Dokument nicht gefunden."), 404
    p = speicher.DATEN / d["datei"]
    if not p.exists():
        return jsonify(fehler="Datei fehlt auf dem Server."), 404
    speicher.protokoll("dokument_geladen", session.get("uid"), d["einsatz_id"], art=d["art"])
    return send_file(p, mimetype="application/pdf", download_name=p.name, max_age=0)


@app.route("/foto/<eid>/<fid>")
@anmeldung()
def foto(eid, fid):
    _einsatz_fuer_mich(eid)
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
    if alt and not darf_einsatz(alt, ich()):
        return jsonify(fehler="Dieser Einsatz ist dir nicht zugeteilt."), 403
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
    for k in ("unterschrift", "uebernommen_aus_alter_app"):
        if alt.get(k):
            e[k] = alt[k]
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
                    for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True) and m.get("ipad", True)])


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
    m = ich()
    for e in speicher.einsaetze():
        if not darf_einsatz(e, m):
            continue
        if e.get("status") == "abgeschlossen" and (e.get("abschluss") or {}).get("datum", "9999") < grenze:
            continue
        e = _einsatz_mit_fotos(e)
        e["docs"] = _docs_fuer_app(e["id"])
        es.append(e)
    # alle aktiven: auch wer kein eigenes iPad-Login hat, kann im Team sein (z. B. Faro am iPad von Matthias)
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True) and m.get("baustelle", True)]
    aus = {"ich": _fuer_app(ich()), "mitarbeiter": [_fuer_app(m) for m in leute], "einsaetze": es,
           "strahlgeraete": speicher.stamm("strahlgeraete", []), "typen": speicher.stamm("geraetetypen", {}),
           "strahlmittel": speicher.stamm("strahlmittel", {}), "checkliste": speicher.stamm("checkliste", [])}
    for k in LISTEN:
        aus[k] = speicher.stamm(k, [])
    if (m or {}).get("rolle") != "admin":   # interne Aufträge: eigene und „offen für alle“
        aus["intern"] = [i for i in aus["intern"] if intern_fuer(i, m["id"]) or i.get("erledigt_von") == m["id"]]
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
        weg = set(speicher.stamm("geloescht_im_buero", {}).get(k, []))
        for x in d[k]:
            if not (isinstance(x, dict) and x.get("id") and x["id"] not in weg):
                continue
            if k in ("fahrzeuge", "kompressoren") and x["id"] in alt:
                # Stammdaten (Kennzeichen, Name, Miete, aktiv) pflegt das Büro; vom iPad nur Stände übernehmen,
                # und zwar nie kleiner als bekannt (ein iPad mit altem Stand soll nichts zurückdrehen)
                a_ = alt[x["id"]]
                for feld in ("km", "h"):
                    try:
                        if x.get(feld) is not None and (a_.get(feld) is None or float(x[feld]) > float(a_[feld])):
                            a_[feld] = x[feld]
                    except (TypeError, ValueError):
                        pass
                if isinstance(x.get("saecke"), dict):
                    a_["saecke"] = x["saecke"]
                continue
            vorher = alt.get(x["id"], {})
            neu = {**vorher, **x}
            if k == "intern" and (isinstance(vorher.get("zeiten"), dict) or isinstance(x.get("zeiten"), dict)):
                # gemeinsame Aufgabe: jedes iPad bringt seine eigene Zeit mit, die der anderen bleiben
                neu["zeiten"] = {**(vorher.get("zeiten") or {}), **(x.get("zeiten") or {})}
            alt[x["id"]] = neu
        for wid in (d.get("geloescht") or {}).get(k, []):
            alt.pop(wid, None)
        speicher.stamm_setzen(k, list(alt.values()))
    return jsonify(ok=True)


@app.route("/api/einsatz/<eid>/foto", methods=["POST"])
@anmeldung()
def api_foto(eid):
    _einsatz_fuer_mich(eid)
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
    _einsatz_fuer_mich(eid)
    sig = request.get_json(force=True)
    datum = _abschluss_datum(sig.get("datum"))
    ls_name, fd_name, versand, lex_pdf = abschluss_ausfuehren(eid, sig, datum, versenden=True)
    speicher.protokoll("job_abgeschlossen", session.get("uid"), eid, ohne_kunde=bool(sig.get("ohne_kunde")))
    return jsonify(ok=True, lieferschein=ls_name, fotodoku=fd_name, versand=versand, docs=_docs_fuer_app(eid),
                   lexware=bool(lex_pdf))


def _dokumente_bauen(eid: str, sig: dict, datum: str):
    """Fotodoku und Lieferschein erzeugen und ablegen. sig["bild"] als Bytes."""
    e = _einsatz_mit_fotos(speicher.einsatz(eid))
    lex = speicher.pfad("dokumente", eid, "lexware.pdf")
    lex_pdf = lex.read_bytes() if lex.exists() else None
    stamm = {"mitarbeiter": {m["id"]: m for m in speicher.stamm("mitarbeiter", [])},
             "strahlgeraete": {g["id"]: g for g in speicher.stamm("strahlgeraete", [])},
             "typen": speicher.stamm("geraetetypen", {})}
    e["ls"] = e.get("ls") or (lex_pdf and lieferschein.lexware_nummer(lex_pdf)) or None
    docs = dok_abschluss.einsatz_dokumente(e, stamm, _quelle(eid), lex_pdf, sig, heute=datum)
    fd_name, fd_pdf, fd_info = docs["fotodoku"]
    ls_name, ls_pdf, ls_info = docs["lieferschein"]
    nr = basis.nummer_ziffern(e.get("ls"))
    speicher.dokument_speichern(eid, "fotodoku", "FD" + nr, fd_name, fd_pdf, "erstellt", fd_info["pruefsumme"])
    ls_status = "unterschrift_ausstehend" if sig.get("ohne_kunde") else "unterschrieben"
    did = speicher.dokument_speichern(eid, "lieferschein", e.get("ls") or "", ls_name, ls_pdf, ls_status,
                                      basis.pruefsumme(ls_pdf), info=ls_info)
    return e, ls_name, ls_pdf, ls_status, did, fd_name, lex_pdf


def abschluss_ausfuehren(eid: str, sig: dict, datum: str, versenden: bool = True):
    """Job abschließen: Unterschrift merken (für spätere Neuerzeugung), Dokumente bauen,
    Lieferschein ggf. senden, Einsatz auf abgeschlossen setzen, Admins benachrichtigen."""
    sig = dict(sig)
    sig["zeit"] = sig.get("zeit") or basis.jetzt_text()
    bild_b64 = sig["bild"].split(",")[-1] if isinstance(sig.get("bild"), str) else None
    if bild_b64:
        sig["bild"] = base64.b64decode(bild_b64)
    e, ls_name, ls_pdf, ls_status, did, fd_name, lex_pdf = _dokumente_bauen(eid, sig, datum)
    versand = None
    if versenden and sig.get("versand") and sig.get("mail") and not sig.get("spaeter"):
        versand = mail.senden(sig["mail"], f"Lieferschein {e.get('ls') or ''} {e['einsatzort']}".strip(),
                              "Guten Tag,\n\nanbei der unterschriebene Lieferschein.\n\nMit freundlichen Grüßen\n"
                              + basis.config()["firma"], [(ls_name, ls_pdf)], eid)
        speicher.dokument_status(did, ls_status, sig["mail"])
    e = speicher.einsatz(eid)
    if lex_pdf and not e.get("ls"):
        e["ls"] = lieferschein.lexware_nummer(lex_pdf)
    e["unterschrift"] = {k: sig.get(k) for k in ("name", "zeit", "eingesehen", "zeiten_auf_ls", "ohne_kunde")}
    e["unterschrift"]["bild"] = bild_b64
    e["status"] = "abgeschlossen"
    e["ergebnis"] = "ausstehend" if sig.get("ohne_kunde") else "unterschrieben"   # für andere iPads
    e["abschluss"] = {"datum": datum, "name": sig.get("name"), "ohneKunde": bool(sig.get("ohne_kunde"))}
    speicher.einsatz_speichern(e)
    admins = [m for m in speicher.stamm("mitarbeiter", []) if m.get("rolle") == "admin" and m.get("mail")]
    for a in admins:
        mail.senden(a["mail"], f"Fotobericht zur Freigabe: {e['einsatzort']}",
                    f"Der Job {e['einsatzort']} ist abgeschlossen. Der Fotobericht wartet auf deine Freigabe.", [], eid)
    return ls_name, fd_name, versand, lex_pdf


# ---------------------------------------------------------------- Büro: Übernahme aus der alten App
@app.route("/buero/altdaten", methods=["GET", "POST"])
@anmeldung(admin=True)
def altdaten_seite():
    if request.method == "GET":
        return render_template("altdaten.html", schritt=1)
    datei = request.files.get("datei")
    fotos = [f for f in request.files.getlist("fotos") if f and f.filename]
    if not datei or not datei.filename:
        flash("Bitte die Datei aus „Alles als Datei teilen“ auswählen.", "fehler")
        return redirect(url_for("altdaten_seite"))
    try:
        roh = datei.read()
        einsaetze = altdaten.einsaetze_aus_datei(roh)
    except (ValueError, UnicodeDecodeError) as ex:
        flash(f"Die Datei ließ sich nicht lesen: {ex}", "fehler")
        return redirect(url_for("altdaten_seite"))
    token = speicher.neue_id()
    ordner = speicher.pfad("import", token, "x").parent
    (ordner / "daten.json").write_bytes(roh)
    namen = []
    for f in fotos:
        name = Path(f.filename).name
        (ordner / "fotos").mkdir(exist_ok=True)
        (ordner / "fotos" / name).write_bytes(f.read())
        namen.append(name)
    server_es = [e for e in speicher.einsaetze() if e.get("status") != "abgeschlossen"]
    liste = []
    for i, e in enumerate(einsaetze):
        v = altdaten.vorschau(e, namen)
        v["index"] = i
        ort = (e.get("einsatzort") or "").lower()
        v["vorschlag"] = next((x["id"] for x in server_es if ort and (ort in (x.get("einsatzort") or "").lower()
                                                                       or (x.get("einsatzort") or "").lower() in ort)), "")
        liste.append(v)
    return render_template("altdaten.html", schritt=2, token=token, liste=liste, fotos=len(namen),
                           server_es=server_es)


@app.route("/buero/altdaten/<token>/<int:index>", methods=["POST"])
@anmeldung(admin=True)
def altdaten_uebernehmen(token, index):
    ordner = speicher.DATEN / "import" / Path(token).name
    if not (ordner / "daten.json").exists():
        abort(404)
    e = altdaten.einsaetze_aus_datei((ordner / "daten.json").read_bytes())[index]
    fotos = [(p.name, p.read_bytes()) for p in sorted((ordner / "fotos").glob("*"))] if (ordner / "fotos").exists() else []
    ziel = request.form.get("ziel") or None
    eid, sig, n, fehlt = altdaten.uebernehmen(e, fotos, ziel)
    text = f"{e.get('einsatzort')} übernommen, {n} Fotos zugeordnet."
    if fehlt:
        text += f" {len(fehlt)} Dateien passten zu keiner Anlage (z. B. {fehlt[0]})."
    if sig:
        datum = _abschluss_datum(sig.get("datum"))
        ls_name, fd_name, _, lex_pdf = abschluss_ausfuehren(eid, sig, datum, versenden=False)
        text += f" Erzeugt: {ls_name} und {fd_name}."
        if not lex_pdf:
            text += " Ohne Lexware-Lieferschein – nach dem Hochladen „Neu erzeugen“ klicken."
    flash(text, "ok")
    return redirect(url_for("einsatz_seite", eid=eid))


@app.route("/buero/einsatz/<eid>/neu-erzeugen", methods=["POST"])
@anmeldung(admin=True)
def dokumente_neu(eid):
    """Lieferschein und Fotodoku neu bauen, z. B. wenn der Lexware-Lieferschein erst nach dem
    Abschluss hochgeladen wurde. Nutzt die beim Abschluss gespeicherte Unterschrift."""
    e = _einsatz_oder_404(eid)
    u = e.get("unterschrift")
    if e.get("status") == "vorbereitet" or not u:
        flash("Das geht nur bei Einsätzen mit gespeicherter Unterschrift.", "fehler")
        return redirect(url_for("einsatz_seite", eid=eid))
    sig = {**u, "bild": base64.b64decode(u["bild"]) if u.get("bild") else None}
    datum = (e.get("abschluss") or {}).get("datum") or date.today().isoformat()
    _, ls_name, _, _, _, fd_name, lex_pdf = _dokumente_bauen(eid, sig, datum)
    e = speicher.einsatz(eid)
    war_offen = e.get("status") != "abgeschlossen"
    if war_offen:   # wieder geöffneter Einsatz: mit der gespeicherten Unterschrift abschließen
        if e.get("tage"):
            e["tage"][-1]["fertig"] = True
        e["status"] = "abgeschlossen"
        e["ergebnis"] = "ausstehend" if u.get("ohne_kunde") else "unterschrieben"
        e["abschluss"] = {**(e.get("abschluss") or {}), "datum": datum, "name": u.get("name"),
                          "ohneKunde": bool(u.get("ohne_kunde"))}
        speicher.einsatz_speichern(e)
    speicher.protokoll("dokumente_neu", session.get("uid"), eid, abgeschlossen=war_offen)
    flash(("Abgeschlossen mit der gespeicherten Unterschrift. " if war_offen else "") +
          f"Neu erzeugt: {ls_name} und {fd_name}." + ("" if lex_pdf else " Hinweis: kein Lexware-Lieferschein hochgeladen."), "ok")
    return redirect(url_for("einsatz_seite", eid=eid))


def _unterschrift_felder(u: dict) -> dict:
    """„12.11.2026, 13:03 Uhr“ → {datum: 2026-11-12, uhrzeit: 13:03} fürs Formular."""
    m = re.match(r"(\d\d)\.(\d\d)\.(\d{4}), (\d\d:\d\d)", (u or {}).get("zeit") or "")
    return {"datum": f"{m.group(3)}-{m.group(2)}-{m.group(1)}", "uhrzeit": m.group(4)} if m else {"datum": "", "uhrzeit": ""}


def _zeitraum_auswahl():
    """Von/Bis aus der Adresse (Standard: dieser Monat) und Schnellwahl-Links."""
    heute = date.today()
    erster = heute.replace(day=1)
    vormonat_ende = erster - timedelta(days=1)
    lohn_bis = heute.replace(day=20)  # Stunden gehen um den 20. an den Steuerberater
    lohn_von = (lohn_bis.replace(day=1) - timedelta(days=1)).replace(day=21)
    schnell = [("Dieser Monat", erster, heute), ("Letzter Monat", vormonat_ende.replace(day=1), vormonat_ende),
               ("21.–20. (Lohn)", lohn_von, lohn_bis)]
    def tag(name, standard):
        try:
            return date.fromisoformat(request.args.get(name, "")).isoformat()
        except ValueError:
            return standard.isoformat()
    von, bis = tag("von", erster), tag("bis", heute)
    if bis < von:
        von, bis = bis, von
    return von, bis, schnell


@app.route("/buero/stunden")
@anmeldung(admin=True)
def stunden_seite():
    """Arbeitszeiten je Mitarbeiter, Export als PDF (Steuerberater) und CSV (Arbeitszeitkonto)."""
    from . import auswertung as aw
    von, bis, schnell = _zeitraum_auswahl()
    wer = [w for w in request.args.getlist("wer") if w]
    zeilen = aw.arbeitszeiten(von, bis, wer)
    from urllib.parse import urlencode
    wq = ("&" + urlencode([("wer", w) for w in wer])) if wer else ""
    kurz = f"{von[2:4]}{von[5:7]}{von[8:]}-{bis[2:4]}{bis[5:7]}{bis[8:]}"
    namen = {m["id"]: m.get("vorname", "") for m in speicher.stamm("mitarbeiter", [])}
    zusatz = (" " + ", ".join(namen.get(w, "") for w in wer)) if 0 < len(wer) <= 3 else ""
    if request.args.get("csv"):
        return send_file(io.BytesIO(aw.csv_arbeitszeiten(zeilen)), mimetype="text/csv", as_attachment=True,
                         download_name=f"{kurz} LST Arbeitszeiten{zusatz}.csv")
    gruppen = aw.nach_mitarbeiter(zeilen)
    if request.args.get("pdf"):
        from .dokumente import stunden as dok_stunden
        return send_file(io.BytesIO(dok_stunden.stundennachweis(gruppen, von, bis)), mimetype="application/pdf",
                         download_name=f"{kurz} LST Stundennachweis{zusatz}.pdf")
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)]
    return render_template("stunden.html", von=von, bis=bis, wer=wer, wq=wq, leute=leute, schnell=schnell,
                           gruppen=gruppen, hm=aw.hm, heute=date.today().isoformat())


@app.route("/buero/stunden/eintrag", methods=["POST"])
@anmeldung(admin=True)
def stunden_eintragen():
    """Stunden von Hand eintragen (z. B. Minijobber ohne iPad)."""
    from .auswertung import handzeit
    f = request.form
    zurueck = redirect(url_for("stunden_seite", von=f.get("z_von") or None, bis=f.get("z_bis") or None,
                                wer=[w for w in f.getlist("z_wer") if w] or None) + "#eintragen")
    leute = {m["id"] for m in speicher.stamm("mitarbeiter", [])}
    try:
        tag = date.fromisoformat(f.get("datum", "")).isoformat()
    except ValueError:
        flash("Bitte ein Datum wählen.", "fehler")
        return zurueck
    if f.get("mid") not in leute:
        flash("Bitte einen Mitarbeiter wählen.", "fehler")
        return zurueck
    h = {"id": speicher.neue_id(), "mid": f["mid"], "datum": tag, "taetigkeit": f.get("taetigkeit", "").strip(),
         "von": f.get("von") or None, "bis": f.get("bis") or None, "pause": None, "dauer": None,
         "eingetragen_von": session.get("uid"), "eingetragen_am": time.time()}
    try:
        h["pause"] = int(f.get("pause") or 0)
        roh = (f.get("stunden") or "").strip().replace(",", ".")
        if roh:
            if ":" in roh:
                st, mi = roh.split(":")
                h["dauer"] = int(st) * 60 + int(mi)
            else:
                h["dauer"] = round(float(roh) * 60)
    except ValueError:
        flash("Pause in Minuten und Stunden als z. B. 4,5 oder 4:30 eintragen.", "fehler")
        return zurueck
    if h["dauer"] is None and not (h["von"] and h["bis"]):
        flash("Bitte Von und Bis eintragen oder direkt die Stunden.", "fehler")
        return zurueck
    liste = speicher.stamm("stunden_manuell", [])
    liste.append(h)
    speicher.stamm_setzen("stunden_manuell", liste)
    speicher.protokoll("stunden_eingetragen", session.get("uid"), mitarbeiter=h["mid"], datum=tag, minuten=handzeit(h))
    flash(f"Eingetragen: {basis.datum(tag)}, {handzeit(h) // 60}:{handzeit(h) % 60:02d} h.", "ok")
    return zurueck


@app.route("/buero/stunden/eintrag/<hid>/loeschen", methods=["POST"])
@anmeldung(admin=True)
def stunden_eintrag_loeschen(hid):
    liste = speicher.stamm("stunden_manuell", [])
    speicher.stamm_setzen("stunden_manuell", [h for h in liste if h.get("id") != hid])
    speicher.protokoll("stunden_geloescht", session.get("uid"), eintrag=hid)
    flash("Eintrag gelöscht.", "ok")
    return redirect(request.referrer or url_for("stunden_seite"))


def _im_buero_loeschen(liste: str, eid: str):
    """Löscht einen Listeneintrag und merkt sich die id, damit ein iPad ihn nicht wieder hochlädt."""
    speicher.stamm_setzen(liste, [x for x in speicher.stamm(liste, []) if x.get("id") != eid])
    weg = speicher.stamm("geloescht_im_buero", {})
    weg.setdefault(liste, []).append(eid)
    speicher.stamm_setzen("geloescht_im_buero", weg)


@app.route("/buero/tanken/eintrag", methods=["POST"])
@anmeldung(admin=True)
def tanken_eintragen():
    """Tankvorgang oder Ölkontrolle im Büro erfassen (gleiches Format wie vom iPad)."""
    f = request.form
    def fehler(text):
        return _tanken_anzeigen(f.get("z_von"), f.get("z_bis"), f.get("z_g") or None, fehler=text, eingabe=f)
    def zahl(name):
        """„62,4“ → 62.4, „41.388“ oder „41.388,5“ → Tausenderpunkt, „3421.5“ → 3421.5; Einheiten werden ignoriert"""
        v = re.sub(r"[^\d,.\-]", "", f.get(name) or "")
        if not v:
            return None
        if "," in v:
            v = v.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+", v):
            v = v.replace(".", "")
        return float(v)
    try:
        tag = date.fromisoformat(f.get("datum", "")).isoformat()
        liter, betrag, stand, oel = zahl("l"), zahl("b"), zahl("stand"), zahl("oel_l")
        preis = zahl("p")
        if liter is None and betrag is not None and preis:
            liter = round(betrag / preis, 2)
        elif betrag is None and liter is not None and preis is not None:
            betrag = round(liter * preis, 2)
        elif preis is None and liter and betrag is not None:
            preis = round(betrag / liter, 3)
    except ValueError:
        return fehler("Bitte Datum und Zahlen prüfen (z. B. 62,4).")
    gid = f.get("geraet", "")
    art, _, gnr = gid.partition(":")
    liste_name = {"f": "fahrzeuge", "k": "kompressoren"}.get(art)
    geraete = speicher.stamm(liste_name, []) if liste_name else []
    geraet = next((x for x in geraete if x.get("id") == gnr), None)
    if not geraet:
        return fehler("Bitte Gerät oder Fahrzeug wählen.")
    og = bool(f.get("oel_geprueft")) or bool(oel)
    if not liter and not og:
        return fehler("Bitte Diesel-Liter (oder Betrag und €/Liter) eintragen oder Öl geprüft/nachgefüllt angeben.")
    t = {"id": speicher.neue_id(), "geraet": gid, "l": liter, "b": betrag, "p": preis,
         "km": stand if art == "f" else None, "h": stand if art == "k" else None,
         "oel_geprueft": og, "oel_l": oel, "datum": tag, "einsatz": f.get("einsatz") or None,
         "von": f.get("wer") or session.get("uid"), "im_buero": True}
    liste = speicher.stamm("tanken", [])
    liste.append(t)
    speicher.stamm_setzen("tanken", liste)
    feld = "km" if art == "f" else "h"
    if stand is not None and (geraet.get(feld) is None or stand > float(geraet.get(feld) or 0)):
        geraet[feld] = stand
        speicher.stamm_setzen(liste_name, geraete)
    speicher.protokoll("tanken_im_buero", session.get("uid"), geraet=gid, datum=tag)
    flash(("Tankvorgang" if liter else "Ölkontrolle") + f" vom {basis.datum(tag)} gespeichert ({geraet.get('kz', '')}).", "ok")
    # Zeitraum so wählen, dass der neue Eintrag sicher sichtbar ist; Gerätefilter nur behalten, wenn er passt
    von, bis = f.get("z_von") or tag, f.get("z_bis") or tag
    von, bis = min(von, tag), max(bis, tag)
    g = f.get("z_g") if f.get("z_g") == gid else None
    return redirect(url_for("tanken_seite", von=von, bis=bis, g=g, neu=t["id"]) + "#neu")


@app.route("/buero/tanken/eintrag/<tid>/loeschen", methods=["POST"])
@anmeldung(admin=True)
def tanken_eintrag_loeschen(tid):
    _im_buero_loeschen("tanken", tid)
    speicher.protokoll("tanken_geloescht", session.get("uid"), eintrag=tid)
    flash("Eintrag gelöscht.", "ok")
    return redirect(request.referrer or url_for("tanken_seite"))


@app.route("/buero/geraete", methods=["GET", "POST"])
@anmeldung(admin=True)
def geraete_seite():
    """Fahrzeuge, Kompressoren und Strahlgeräte verwalten (eigen oder gemietet, aktiv/inaktiv)."""
    fz = speicher.stamm("fahrzeuge", [])
    kp = speicher.stamm("kompressoren", [])
    sg = speicher.stamm("strahlgeraete", [])
    typen = speicher.stamm("geraetetypen", {})
    if request.method == "POST":
        f = request.form
        def zahl(v):
            v = re.sub(r"[^\d,.]", "", v or "")
            if not v:
                return None
            if "," in v:
                v = v.replace(".", "").replace(",", ".")
            elif re.fullmatch(r"\d{1,3}(\.\d{3})+", v):
                v = v.replace(".", "")
            try:
                return float(v)
            except ValueError:
                return None
        def neue_id(liste, buchstabe):
            ids, n = {x["id"] for x in liste}, 1
            while f"{buchstabe}{n}" in ids:
                n += 1
            return f"{buchstabe}{n}"
        for liste, b, stand in ((fz, "f", "km"), (kp, "k", "h")):
            for x in liste:
                i = f"{b}_{x['id']}"
                x["kz"] = (f.get(f"{i}_kz") or x.get("kz", "")).strip().upper()
                x["name"] = (f.get(f"{i}_name") or "").strip() or x.get("name", "")
                x["eigen"] = f.get(f"{i}_eigen") == "eigen"
                x["vermieter"] = (f.get(f"{i}_vermieter") or "").strip()
                x["aktiv"] = bool(f.get(f"{i}_aktiv"))
                if (f.get(f"{i}_{stand}") or "").strip():
                    x[stand] = zahl(f.get(f"{i}_{stand}"))
            if (f.get(f"{b}_neu_kz") or "").strip():
                neu = {"id": neue_id(liste, b), "kz": f[f"{b}_neu_kz"].strip().upper(),
                       "name": (f.get(f"{b}_neu_name") or "").strip() or ("Fahrzeug" if b == "f" else "Kompressor"),
                       stand: zahl(f.get(f"{b}_neu_{stand}")), "eigen": f.get(f"{b}_neu_eigen") == "eigen",
                       "vermieter": (f.get(f"{b}_neu_vermieter") or "").strip(), "aktiv": True}
                if b == "f":
                    neu["saecke"] = {}
                liste.append(neu)
        for x in sg:
            i = f"s_{x['id']}"
            x["name"] = (f.get(f"{i}_name") or "").strip() or x["name"]
            x["typ"] = f.get(f"{i}_typ") or x.get("typ")
            x["aktiv"] = bool(f.get(f"{i}_aktiv"))
        if (f.get("s_neu_name") or "").strip():
            sg.append({"id": neue_id(sg, "s"), "name": f["s_neu_name"].strip(), "typ": f.get("s_neu_typ") or "loki",
                       "aktiv": True})
        speicher.stamm_setzen("fahrzeuge", fz)
        speicher.stamm_setzen("kompressoren", kp)
        speicher.stamm_setzen("strahlgeraete", sg)
        speicher.protokoll("geraete_geaendert", session.get("uid"))
        flash("Gespeichert. Die iPads übernehmen die Änderungen beim nächsten Abgleich.", "ok")
        return redirect(url_for("geraete_seite"))
    from .auswertung import zahl as zahl_text
    return render_template("geraete.html", fz=fz, kp=kp, sg=sg, typen=typen, zahl=zahl_text)


@app.route("/buero/tanken")
@anmeldung(admin=True)
def tanken_seite():
    """Tankvorgänge sowie Kompressor-Betriebsstunden und Kilometer je Einsatz."""
    von, bis, _ = _zeitraum_auswahl()
    g = request.args.get("g") or None
    if request.args.get("pdf") or request.args.get("csv"):
        from . import auswertung as aw
        gruppen = aw.tank_summen([t for t in aw.tankvorgaenge(von, bis) if not g or t["gid"] == g])
        kurz = f"{von[2:4]}{von[5:7]}{von[8:]}-{bis[2:4]}{bis[5:7]}{bis[8:]}"
        zusatz = f" {gruppen[0]['geraet']}" if g and gruppen else ""
        if request.args.get("csv"):
            return send_file(io.BytesIO(aw.csv_tanken(gruppen)), mimetype="text/csv", as_attachment=True,
                             download_name=f"{kurz} LST Tanken{zusatz}.csv")
        from .dokumente import tanken as dok_tanken
        return send_file(io.BytesIO(dok_tanken.tankliste(gruppen, von, bis)), mimetype="application/pdf",
                         download_name=f"{kurz} LST Tanken{zusatz}.pdf")
    return _tanken_anzeigen(von, bis, g, neu=request.args.get("neu"))


def _tanken_anzeigen(von, bis, g, fehler=None, eingabe=None, neu=None):
    from . import auswertung as aw
    _, _, schnell = _zeitraum_auswahl()
    tank = [t for t in aw.tankvorgaenge(von, bis) if not g or t["gid"] == g]
    geraete = [x for x in aw.geraete_je_einsatz(von, bis) if not g or g in (x["k_id"], x["f_id"])]
    letzte = sorted((e for e in speicher.einsaetze() if e.get("art", "kunde") != "intern"),
                    key=lambda e: e.get("von") or "", reverse=True)[:15]
    leute = [m for m in speicher.stamm("mitarbeiter", []) if m.get("aktiv", True)]
    return render_template("tanken.html", von=von, bis=bis, schnell=schnell, g=g, auswahl=aw.geraete_auswahl(),
                           heute=date.today().isoformat(), einsaetze=letzte, leute=leute,
                           geraete=geraete, gruppen=aw.tank_summen(tank), zahl=aw.zahl,
                           fehler=fehler, ein=eingabe or {}, neu=neu)


@app.route("/buero/einsatz/<eid>/unterschrift", methods=["POST"])
@anmeldung(admin=True)
def unterschrift_aendern(eid):
    """Name, Datum und Uhrzeit der gespeicherten Unterschrift korrigieren (z. B. nach Übernahme aus der
    alten App, die die Uhrzeit nicht kannte). Abgeschlossene Einsätze bekommen gleich neue Dokumente."""
    e = _einsatz_oder_404(eid)
    if not e.get("unterschrift"):
        abort(404)
    f = request.form
    try:
        tag = date.fromisoformat(f.get("datum", ""))
        h, mi = (int(x) for x in f.get("uhrzeit", "").split(":")[:2])
        assert 0 <= h < 24 and 0 <= mi < 60
    except (ValueError, AssertionError):
        flash("Bitte Datum und Uhrzeit vollständig eintragen.", "fehler")
        return redirect(url_for("einsatz_seite", eid=eid))
    e["unterschrift"]["zeit"] = f"{tag:%d.%m.%Y}, {h:02d}:{mi:02d} Uhr"
    if f.get("name", "").strip():
        e["unterschrift"]["name"] = f["name"].strip()
    e["abschluss"] = {**(e.get("abschluss") or {}), "datum": tag.isoformat(), "name": e["unterschrift"]["name"]}
    speicher.einsatz_speichern(e)
    speicher.protokoll("unterschrift_geaendert", session.get("uid"), eid, zeit=e["unterschrift"]["zeit"])
    if e.get("status") == "abgeschlossen":
        return dokumente_neu(eid)
    flash(f"Unterschrift geändert: {e['unterschrift']['name']}, {e['unterschrift']['zeit']}.", "ok")
    return redirect(url_for("einsatz_seite", eid=eid))


@app.route("/buero/einsatz/<eid>/wieder-oeffnen", methods=["POST"])
@anmeldung(admin=True)
def einsatz_wieder_oeffnen(eid):
    """Abgeschlossenen Einsatz wieder bearbeitbar machen. Die Unterschrift bleibt gespeichert;
    danach im Büro „Mit gespeicherter Unterschrift abschließen“."""
    e = _einsatz_oder_404(eid)
    if e.get("status") != "abgeschlossen":
        return redirect(url_for("einsatz_seite", eid=eid))
    e["status"] = "inarbeit"
    e.pop("ergebnis", None)
    if e.get("tage"):
        e["tage"][-1]["fertig"] = False
    speicher.einsatz_speichern(e)
    speicher.protokoll("einsatz_wieder_geoeffnet", session.get("uid"), eid)
    flash("Wieder geöffnet. Jetzt auf dem iPad (oder hier unter „Bearbeiten“) anpassen, danach "
          "„Mit gespeicherter Unterschrift abschließen“." if e.get("unterschrift") else
          "Wieder geöffnet. Es ist keine Unterschrift gespeichert – zum Abschluss auf dem iPad neu unterschreiben lassen.", "ok")
    return redirect(url_for("einsatz_seite", eid=eid))


@app.route("/buero/einsatz/<eid>/senden", methods=["POST"])
@anmeldung(admin=True)
def dokumente_senden(eid):
    e = _einsatz_oder_404(eid)
    f = request.form
    gewaehlt = [d for d in speicher.dokumente(eid) if d["id"] in f.getlist("doc")]
    liste = empfaenger_liste(*f.getlist("an"), f.get("weitere", ""))
    falsch = [a for a in liste if not adresse_ok(a)]
    if not gewaehlt or not liste or falsch:
        flash("Bitte mindestens ein Dokument und einen Empfänger wählen." if not falsch else
              f"Diese Adresse sieht unvollständig aus: {falsch[0]}", "fehler")
        return redirect(url_for("einsatz_seite", eid=eid) + "#senden")
    erg = _dokumente_mailen(e, gewaehlt, liste, f.get("betreff"), f.get("text", ""))
    flash(f"{len(gewaehlt)} Dokument(e) an {', '.join(liste)} versendet." if erg["status"] == "versendet" else
          f"Nicht versendet, liegt im Postausgang: {erg['fehler']}", "ok" if erg["status"] == "versendet" else "hinweis")
    return redirect(url_for("einsatz_seite", eid=eid))


def _dokumente_mailen(e: dict, gewaehlt: list[dict], liste: list[str], betreff: str | None, text: str) -> dict:
    anh = [(Path(d["datei"]).name, (speicher.DATEN / d["datei"]).read_bytes()) for d in gewaehlt]
    erg = mail.senden(", ".join(liste), betreff or f"Unterlagen {e['einsatzort']}", text or "", anh, e["id"])
    for d in gewaehlt:
        speicher.dokument_status(d["id"], "versendet" if erg["status"] == "versendet" else d["status"], ", ".join(liste))
    speicher.protokoll("dokumente_gesendet", session.get("uid"), e["id"], an=liste, docs=[d["id"] for d in gewaehlt],
                       mail=erg["status"])
    return erg


@app.route("/api/kontakte")
@anmeldung()
def api_kontakte():
    """Ohne Suchbegriff: Personen der Firmen des Einsatzes. Mit Suchbegriff: Treffer in Name, Firma, Mail."""
    alle = speicher.stamm("kontakte", [])
    q = (request.args.get("q") or "").strip().lower()
    if q:
        treffer = [k for k in alle if all(w in f"{k['name']} {k['firma']} {k['mail']}".lower() for w in q.split())]
    else:
        e = speicher.einsatz(request.args.get("eid", ""))
        if not darf_einsatz(e, ich()):
            return jsonify([])
        fids = set(((e or {}).get("notion") or {}).get("firmen") or [])
        namen = {x.lower() for x in ((e or {}).get("kunde"), (e or {}).get("einsatzort")) if x}
        treffer = [k for k in alle if fids & set(k.get("firmen") or []) or
                   any(n and (n in k["firma"].lower() or k["firma"].lower() in n) for n in namen if k["firma"])]
    return jsonify([{"name": k["name"], "mail": k["mail"], "firma": k["firma"]} for k in treffer[:30]])


@app.route("/api/einsatz/<eid>/senden", methods=["POST"])
@anmeldung()
def api_dokumente_senden(eid):
    """Vom iPad: Lieferschein/Fotodoku an ausgewählte Empfänger mailen."""
    e = _einsatz_fuer_mich(eid)
    d = request.get_json(force=True)
    gewaehlt = [x for x in speicher.dokumente(eid) if x["id"] in (d.get("docs") or []) and x["art"] in APP_DOKUMENTE]
    liste = empfaenger_liste(*(d.get("an") or []), d.get("weitere", ""))
    falsch = [a for a in liste if not adresse_ok(a)]
    if not gewaehlt:
        return jsonify(fehler="Bitte mindestens ein Dokument wählen."), 400
    if not liste or falsch:
        return jsonify(fehler=f"Diese Adresse sieht unvollständig aus: {falsch[0]}" if falsch else "Bitte mindestens einen Empfänger wählen."), 400
    erg = _dokumente_mailen(e, gewaehlt, liste, d.get("betreff"), d.get("text", ""))
    return jsonify(ok=True, status=erg["status"], fehler=erg.get("fehler"), an=liste)


@app.route("/buero/dokument/<did>/loeschen", methods=["POST"])
@anmeldung(admin=True)
def dokument_loeschen(did):
    d = speicher.dokument_loeschen(did)
    if not d:
        abort(404)
    speicher.protokoll("dokument_geloescht", session.get("uid"), d["einsatz_id"], datei=d["datei"])
    flash(f"Gelöscht: {Path(d['datei']).name}", "ok")
    return redirect(url_for("einsatz_seite", eid=d["einsatz_id"]))


def main():
    speicher.einrichten()
    app.secret_key = speicher.geheim()["sitzung"]
    threading.Thread(target=_aufgaben_takt, name="notion-aufgaben", daemon=True).start()
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
