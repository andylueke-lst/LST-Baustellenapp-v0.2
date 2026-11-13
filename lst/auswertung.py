"""Auswertung fürs Büro: Arbeitszeiten, Kompressor- und Kilometerstände je Einsatz, Tankvorgänge.

Die Zeitberechnung entspricht der iPad-App (calc/adj/minPause im Prototyp), damit Büro und
iPad dieselben Stunden zeigen.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from . import speicher


# ---------------------------------------------------------------- Zeitberechnung wie auf dem iPad
def _min(t: str | None) -> int | None:
    try:
        h, m = t.split(":")[:2]
        return int(h) * 60 + int(m)
    except (AttributeError, ValueError):
        return None


def zahl(x, stellen: int = 1) -> str:
    """3420.5 → „3.420,5“, 41388.0 → „41.388“"""
    if x is None or x == "":
        return "–"
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    t = f"{x:,.{stellen}f}" if x != int(x) or stellen >= 2 else f"{x:,.0f}"
    return t.replace(",", "#").replace(".", ",").replace("#", ".")


def hm(m: int | None) -> str:
    if m is None:
        return "–"
    return f"{'-' if m < 0 else ''}{abs(m) // 60}:{abs(m) % 60:02d}"


def _labels(z: dict) -> dict:
    hotel_a, hotel_e = z.get("start") == "hotel", z.get("ende") == "hotel"
    return {1: None if hotel_a else "Ankunft Büro", 2: "Abfahrt Hotel" if hotel_a else "Abfahrt zur Baustelle",
            3: "Ankunft Baustelle", 4: "Abfahrt von Baustelle", 5: "Ankunft Hotel" if hotel_e else "Ankunft Büro",
            6: None if hotel_e else "Feierabend"}


def _p(z: dict, k: int) -> str | None:
    p = z.get("p") or {}
    return p.get(str(k)) or p.get(k)


def _pause_ok(z: dict) -> bool:
    return z["pauseOk"] if z.get("pauseOk") is not None else z.get("pause") is not None


def min_pause(b: int | None) -> int:
    if b is None:
        return 0
    n = 0
    if b > 570:
        n = 45
    elif b > 540:
        n = max(30, b - 540)
    elif b > 390:
        n = 30
    elif b > 360:
        n = b - 360
    return -(-n // 15) * 15


def _adj(z: dict) -> dict:
    L = _labels(z)
    nacht = (_min(_p(z, 3)) or 0) >= 960
    r, off, prev = {}, 0, None
    for k in range(1, 7):
        if not L[k] or not _p(z, k):
            continue
        m = _min(_p(z, k))
        if m is None:
            continue
        m += off
        if prev is not None and m < prev and nacht and off == 0:
            off = 1440
            m += 1440
        r[k] = m
        prev = m
    return r


def berechnen(z: dict) -> dict:
    A, L = _adj(z), _labels(z)
    p = A.get
    def d(a, b):
        return p(b) - p(a) if p(a) is not None and p(b) is not None else 0
    ruest = (d(1, 2) if L[1] else 0) + (d(5, 6) if L[6] else 0)
    fahrt = d(2, 3) + d(4, 5)
    ort = d(3, 4) if p(3) is not None and p(4) is not None else None
    erst = p(1) if L[1] else p(2)
    letzt = p(6) if L[6] else p(5)
    brutto = letzt - erst if erst is not None and letzt is not None else None
    est = brutto if brutto is not None else (max(A.values()) - erst if erst is not None and A else None)
    pause = z.get("pause") if _pause_ok(z) else min_pause(est)
    pause = pause or 0
    fehlt = [l for k, l in L.items() if l and not _p(z, k)] + ([] if _pause_ok(z) else ["Pause bestätigen"])
    return {"beginn": _p(z, 1) if L[1] else _p(z, 2), "ende": _p(z, 6) if L[6] else _p(z, 5),
            "ruest": ruest, "fahrt": fahrt, "ort": None if ort is None else ort - pause,
            "gesamt": None if brutto is None else brutto - pause, "pause": pause, "fehlt": fehlt}


# ---------------------------------------------------------------- Auswertung
def handzeit(h: dict) -> int | None:
    """Von Hand eingetragene Stunden: entweder Von/Bis abzüglich Pause oder direkt eine Dauer (Minuten)."""
    if h.get("dauer") is not None:
        return int(h["dauer"])
    a, b = _min(h.get("von")), _min(h.get("bis"))
    if a is None or b is None:
        return None
    return max(0, (b - a) % 1440 - int(h.get("pause") or 0))


def _im_zeitraum(tag: str | None, von: str, bis: str) -> bool:
    return bool(tag) and von <= tag[:10] <= bis


def _namen() -> dict:
    return {m["id"]: (m.get("vorname", "") + " " + (m.get("nachname") or "")).strip()
            for m in speicher.stamm("mitarbeiter", [])}


def arbeitszeiten(von: str, bis: str, wer: str | list[str] | None = None) -> list[dict]:
    """wer: eine Mitarbeiter-id, mehrere oder None für alle."""
    wer = {wer} if isinstance(wer, str) else set(wer or [])
    namen = _namen()
    zeilen = []
    for e in speicher.einsaetze():
        for t in e.get("tage") or []:
            if not _im_zeitraum(t.get("datum"), von, bis):
                continue
            for mid, z in (t.get("zeiten") or {}).items():
                if wer and mid not in wer:
                    continue
                c = berechnen(z or {})
                zeilen.append({"datum": t["datum"], "mid": mid, "name": namen.get(mid, mid), "art": "Einsatz",
                               "wo": e.get("name") or e.get("einsatzort") or e.get("kunde") or "", "eid": e["id"],
                               "start_hotel": z.get("start") == "hotel", "ende_hotel": z.get("ende") == "hotel",
                               "offen": not t.get("fertig"), **c})
    einsatz_ort = {e["id"]: e.get("name") or e.get("einsatzort", "") for e in speicher.einsaetze()}
    arten = {"anreise": "Anreise", "abreise": "Abreise", "wechsel": "Reise zwischen Jobs"}
    for r in speicher.stamm("reisetage", []):
        if not isinstance(r, dict) or not _im_zeitraum(r.get("datum"), von, bis):
            continue
        a, b = _min(r.get("ab")), _min(r.get("an"))
        dauer = b - a if a is not None and b is not None and b > a else None
        for mid in r.get("ma") or []:
            if wer and mid not in wer:
                continue
            zeilen.append({"datum": r["datum"], "mid": mid, "name": namen.get(mid, mid),
                           "art": arten.get(r.get("art"), "Reise"), "wo": einsatz_ort.get(r.get("einsatz"), ""),
                           "eid": r.get("einsatz"), "beginn": r.get("ab"), "ende": r.get("an"), "ruest": 0,
                           "fahrt": dauer or 0, "ort": None, "pause": 0, "gesamt": dauer,
                           "fehlt": [] if dauer else ["Abfahrt/Ankunft"], "offen": False})
    for h in speicher.stamm("stunden_manuell", []):
        mid = (h or {}).get("mid")
        if not _im_zeitraum(h.get("datum"), von, bis) or (wer and mid not in wer):
            continue
        c = handzeit(h)
        zeilen.append({"datum": h["datum"], "mid": mid, "name": namen.get(mid, mid or "?"), "art": "Händisch",
                       "wo": h.get("taetigkeit", ""), "eid": None, "hid": h["id"], "beginn": h.get("von"),
                       "ende": h.get("bis"), "ruest": 0, "fahrt": 0, "ort": c, "pause": h.get("pause") or 0,
                       "gesamt": c, "fehlt": [], "offen": False})
    for i in speicher.stamm("intern", []):
        if not isinstance(i, dict):
            continue
        eintraege = list((i.get("zeiten") or {}).items()) or [(i.get("erledigt_von") or i.get("zust"), i.get("zeit") or {})]
        for mid, z in eintraege:
            z = z or {}
            if not z.get("von") or not _im_zeitraum(z.get("datum"), von, bis) or (wer and mid not in wer):
                continue
            a, b = _min(z.get("von")), _min(z.get("bis"))
            dauer = (b - a) % 1440 if a is not None and b is not None else None
            zeilen.append({"datum": z["datum"], "mid": mid, "name": namen.get(mid, mid or "?"), "art": "Intern",
                           "wo": i.get("titel", ""), "eid": None, "beginn": z.get("von"), "ende": z.get("bis"),
                           "ruest": 0, "fahrt": 0, "ort": dauer, "pause": 0, "gesamt": dauer,
                           "fehlt": [] if dauer is not None else ["Ende"], "offen": dauer is None})
    zeilen.sort(key=lambda x: (x["datum"], x["name"], x.get("beginn") or ""))
    return zeilen


def summen(zeilen: list[dict]) -> list[dict]:
    s = {}
    for z in zeilen:
        x = s.setdefault(z["mid"], {"name": z["name"], "tage": set(), "gesamt": 0, "ort": 0, "fahrt": 0,
                                    "reise": 0, "intern": 0, "hand": 0, "offen": 0})
        x["tage"].add(z["datum"])
        if z["gesamt"] is None or z["fehlt"]:
            x["offen"] += 1
        g = z["gesamt"] or 0
        x["gesamt"] += g
        if z["art"] == "Einsatz":
            x["ort"] += z["ort"] or 0
            x["fahrt"] += z["fahrt"]
        elif z["art"] == "Intern":
            x["intern"] += g
        elif z["art"] == "Händisch":
            x["hand"] += g
        else:
            x["reise"] += g
    return sorted(({**v, "tage": len(v["tage"])} for v in s.values()), key=lambda v: v["name"])


def geraete_je_einsatz(von: str, bis: str) -> list[dict]:
    fz = {f["id"]: f for f in speicher.stamm("fahrzeuge", [])}
    kp = {k["id"]: k for k in speicher.stamm("kompressoren", [])}
    aus = []
    for e in speicher.einsaetze():
        tage = [t.get("datum") for t in e.get("tage") or [] if t.get("datum")]
        anfang, ende = min([e.get("von") or "9999"] + tage), max([e.get("bis") or e.get("von") or ""] + tage)
        if anfang > bis or ende < von:
            continue
        g, st, en = e.get("geraete") or {}, e.get("start") or {}, e.get("ende") or {}
        if not st and not en:
            continue
        k, f = kp.get(g.get("kompressor")), fz.get(g.get("fahrzeug"))
        def diff(a, b):
            try:
                return round(float(b) - float(a), 1)
            except (TypeError, ValueError):
                return None
        aus.append({"eid": e["id"], "wo": e.get("name") or e.get("einsatzort", ""), "k_id": "k:" + k["id"] if k else None,
                    "f_id": "f:" + f["id"] if f else None, "von": anfang, "bis": ende,
                    "status": e.get("status"),
                    "kompressor": f"{k.get('kz', '')} {'' if k.get('eigen') else '(Miete)'}".strip() if k else "",
                    "h_start": st.get("kompH") if k else None, "h_ende": en.get("kompH") if k else None,
                    "h": diff(st.get("kompH"), en.get("kompH")) if k else None,
                    "fahrzeug": f"{f.get('kz', '')} {f.get('name', '')}".strip() if f else "",
                    "km_start": st.get("km"), "km_ende": en.get("km"), "km": diff(st.get("km"), en.get("km"))})
    aus.sort(key=lambda x: x["von"])
    return aus


def _betrag(t: dict):
    if t.get("b") is not None:
        return t["b"]
    return round(t["l"] * t["p"], 2) if t.get("l") and t.get("p") else None


def _preis(t: dict):
    if t.get("p"):
        return t["p"]
    return round(t["b"] / t["l"], 3) if t.get("l") and t.get("b") else None


def tankvorgaenge(von: str, bis: str) -> list[dict]:
    namen = _namen()
    geraet = {"f:" + f["id"]: (f"{f.get('kz', '')} {f.get('name', '')}".strip(), "km")
              for f in speicher.stamm("fahrzeuge", [])}
    geraet.update({"k:" + k["id"]: (f"{k.get('kz', '')} {'Kompressor' if k.get('eigen') else 'Mietkompressor'}", "h")
                   for k in speicher.stamm("kompressoren", [])})
    orte = {e["id"]: e.get("name") or e.get("einsatzort", "") for e in speicher.einsaetze()}
    aus = []
    alle = []
    for t in speicher.stamm("tanken", []):
        if not isinstance(t, dict) or not t.get("datum"):
            continue
        name, art = geraet.get(t.get("geraet"), (t.get("geraet") or "?", "km"))
        stand = t.get("h") if art == "h" else t.get("km")
        if art == "h" and stand is None and t.get("km") is not None:
            stand = t.get("km")  # ältere Einträge: Stand stand im Kilometerfeld
        try:
            if stand is not None and float(stand) <= 0:
                stand = None  # 0 = Zähler defekt/unbekannt, zählt nicht für den Verbrauch
        except (TypeError, ValueError):
            stand = None
        alle.append({"datum": t["datum"], "gid": t.get("geraet"), "geraet": name, "art": art, "liter": t.get("l"),
                    "betrag": _betrag(t), "preis": _preis(t), "oel_geprueft": bool(t.get("oel_geprueft") or t.get("oel_l")),
                    "oel_l": t.get("oel_l"), "id": t.get("id"),
                    "stand": stand, "wer": namen.get(t.get("von"), t.get("von") or ""),
                    "wo": orte.get(t.get("einsatz"), ""), "eid": t.get("einsatz")})
    verbrauch_rechnen(alle)
    aus = [t for t in alle if _im_zeitraum(t["datum"], von, bis)]
    aus.sort(key=lambda x: (x["datum"], x["geraet"]))
    return aus


def verbrauch_rechnen(alle: list[dict]):
    """Verbrauch nach der Volltank-Methode: getankte Liter geteilt durch die Strecke (bzw. Stunden)
    seit dem vorigen Tanken desselben Geräts. Sprinter in l/100 km, Kompressor in l/h.
    Ölkontrollen ohne Diesel zählen nicht als Tankvorgang. Rechnet über die ganze Historie,
    damit auch der erste Tankvorgang im Zeitraum einen Vorgänger hat."""
    je = {}
    for t in alle:
        if t["liter"] and t["stand"] is not None:
            je.setdefault(t["gid"], []).append(t)
    for liste in je.values():
        liste.sort(key=lambda t: (float(t["stand"]), t["datum"]))
        vorher = None
        for t in liste:
            t["seit"] = None
            if vorher is not None:
                d = float(t["stand"]) - float(vorher["stand"])
                if d > 0:
                    t["seit"] = d
                    t["verbrauch"] = float(t["liter"]) / d * (100 if t["art"] == "km" else 1)
            vorher = t


def tank_summen(tank: list[dict]) -> list[dict]:
    s = {}
    for t in tank:
        x = s.setdefault(t["gid"], {"gid": t["gid"], "geraet": t["geraet"], "art": t["art"], "anzahl": 0, "liter": 0.0,
                                    "betrag": 0.0, "oel_l": 0.0, "oel_kontrollen": 0, "eintraege": []})
        x["eintraege"].append(t)
        x["anzahl"] += 1 if t["liter"] else 0
        x["oel_kontrollen"] += 1 if t["oel_geprueft"] else 0
        x["oel_l"] += float(t["oel_l"] or 0)
        x["liter"] += float(t["liter"] or 0)
        x["betrag"] += float(t["betrag"] or 0)
        if t.get("verbrauch") is not None:
            x["v_liter"] = x.get("v_liter", 0.0) + float(t["liter"])
            x["v_strecke"] = x.get("v_strecke", 0.0) + t["seit"]
    for x in s.values():
        if x.get("v_strecke"):
            x["verbrauch"] = x["v_liter"] / x["v_strecke"] * (100 if x["art"] == "km" else 1)
    return sorted(s.values(), key=lambda x: (x["art"] != "km", x["geraet"]))


def geraete_auswahl() -> list[tuple[str, str]]:
    return ([("f:" + f["id"], f"{f.get('kz', '')} {f.get('name', '')}".strip()) for f in speicher.stamm("fahrzeuge", [])]
            + [("k:" + k["id"], f"{k.get('kz', '')} {'Kompressor' if k.get('eigen') else 'Mietkompressor'}")
               for k in speicher.stamm("kompressoren", [])])


def nach_mitarbeiter(zeilen: list[dict]) -> list[dict]:
    """Zeilen je Mitarbeiter (Reihenfolge wie in der Mitarbeiterliste), jeweils nach Datum sortiert, mit Summe."""
    reihenfolge = {m["id"]: n for n, m in enumerate(speicher.stamm("mitarbeiter", []))}
    gruppen = {}
    for z in zeilen:
        gruppen.setdefault(z["mid"], []).append(z)
    aus = []
    for mid, zz in sorted(gruppen.items(), key=lambda kv: (reihenfolge.get(kv[0], 99), kv[1][0]["name"])):
        zz.sort(key=lambda z: (z["datum"], z.get("beginn") or ""))
        for z in zz:
            z["datum_d"] = date.fromisoformat(z["datum"][:10])
        aus.append({"mid": mid, "name": zz[0]["name"], "zeilen": zz, "summe": summen(zz)[0]})
    return aus


def _dez(m: int | None) -> str:
    return f"{m / 60:.2f}".replace(".", ",") if m is not None else ""


def csv_arbeitszeiten(zeilen: list[dict]) -> bytes:
    """Für Excel (Semikolon, deutsche Kommazahlen). Dauern in Dezimalstunden zum Weiterrechnen."""
    from .dokumente.basis import WOCHENTAGE
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Mitarbeiter", "Datum", "Wochentag", "Art", "Einsatz/Aufgabe", "Beginn", "Ende",
                "Pause (Std.)", "Rüstzeit (Std.)", "Fahrt (Std.)", "vor Ort (Std.)", "Gesamt (Std.)", "Gesamt (h:mm)",
                "Hinweis"])
    for g in nach_mitarbeiter(zeilen):
        for z in g["zeilen"]:
            d = date.fromisoformat(z["datum"])
            w.writerow([z["name"], d.strftime("%d.%m.%Y"), WOCHENTAGE[d.weekday()], z["art"], z["wo"],
                        z.get("beginn") or "", z.get("ende") or "", _dez(z["pause"]), _dez(z["ruest"]),
                        _dez(z["fahrt"]), _dez(z["ort"]), _dez(z["gesamt"]), hm(z["gesamt"]),
                        "fehlt: " + ", ".join(z["fehlt"]) if z["fehlt"] else ""])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def csv_tanken(gruppen: list[dict]) -> bytes:
    """Alle Einträge je Gerät für Excel (Semikolon, deutsche Kommazahlen)."""
    def k(x, st=2):
        return f"{float(x):.{st}f}".replace(".", ",") if x not in (None, "") else ""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Gerät/Fahrzeug", "Datum", "Stand", "Einheit Stand", "Diesel (l)", "Betrag (€)", "€/Liter",
                "Seit letztem Tanken", "Verbrauch", "Einheit Verbrauch", "Öl geprüft", "Öl nachgefüllt (l)",
                "Wer", "Einsatz"])
    for x in gruppen:
        km = x["art"] == "km"
        for t in x["eintraege"]:
            w.writerow([x["geraet"], date.fromisoformat(t["datum"]).strftime("%d.%m.%Y"), k(t["stand"], 1),
                        "km" if km else "Std.", k(t["liter"]), k(t["betrag"]), k(t.get("preis"), 3),
                        k(t.get("seit"), 1), k(t.get("verbrauch"), 2), "l/100 km" if km else "l/h",
                        "ja" if t["oel_geprueft"] else "", k(t["oel_l"]), t["wer"], t["wo"]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def letzte_staende() -> dict:
    """Höchster bekannter Kilometer- bzw. Betriebsstundenstand je Gerät, mit Datum und Herkunft.
    Quellen: Tankvorgänge/Ölkontrollen, Start- und Endstände der Einsätze, gespeicherter Stand."""
    aus = {}
    def merken(gid, stand, datum, quelle):
        try:
            stand = float(str(stand).replace(".", "").replace(",", ".")) if isinstance(stand, str) and "," in stand \
                else float(stand)
        except (TypeError, ValueError):
            return
        if stand <= 0:
            return
        alt = aus.get(gid)
        if not alt or stand > alt["stand"] or (stand == alt["stand"] and (datum or "") > (alt["datum"] or "")):
            aus[gid] = {"stand": stand, "datum": datum, "quelle": quelle}
    for t in speicher.stamm("tanken", []):
        if not isinstance(t, dict):
            continue
        g = t.get("geraet") or ""
        merken(g, t.get("km") if g.startswith("f:") else (t.get("h") if t.get("h") is not None else t.get("km")),
               t.get("datum"), "Ölkontrolle" if not t.get("l") else "Tanken")
    for e in speicher.einsaetze():
        g = e.get("geraete") or {}
        tage = [t.get("datum") for t in e.get("tage") or [] if t.get("datum")]
        anfang = min(tage) if tage else e.get("von")
        ende = (e.get("abschluss") or {}).get("datum") or (max(tage) if tage else e.get("bis"))
        name = e.get("name") or e.get("einsatzort") or ""
        if g.get("fahrzeug"):
            merken("f:" + g["fahrzeug"], (e.get("start") or {}).get("km"), anfang, f"Start {name}")
            merken("f:" + g["fahrzeug"], (e.get("ende") or {}).get("km"), ende, f"Ende {name}")
        if g.get("kompressor"):
            merken("k:" + g["kompressor"], (e.get("start") or {}).get("kompH"), anfang, f"Start {name}")
            merken("k:" + g["kompressor"], (e.get("ende") or {}).get("kompH"), ende, f"Ende {name}")
    for f in speicher.stamm("fahrzeuge", []):
        merken("f:" + f["id"], f.get("km"), None, "zuletzt bekannt")
    for k in speicher.stamm("kompressoren", []):
        merken("k:" + k["id"], k.get("h"), None, "zuletzt bekannt")
    return aus
