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
    t = f"{x:,.{stellen}f}" if x != int(x) or stellen == 2 else f"{x:,.0f}"
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
def _im_zeitraum(tag: str | None, von: str, bis: str) -> bool:
    return bool(tag) and von <= tag[:10] <= bis


def _namen() -> dict:
    return {m["id"]: (m.get("vorname", "") + " " + (m.get("nachname") or "")).strip()
            for m in speicher.stamm("mitarbeiter", [])}


def arbeitszeiten(von: str, bis: str, wer: str | None = None) -> list[dict]:
    namen = _namen()
    zeilen = []
    for e in speicher.einsaetze():
        for t in e.get("tage") or []:
            if not _im_zeitraum(t.get("datum"), von, bis):
                continue
            for mid, z in (t.get("zeiten") or {}).items():
                if wer and mid != wer:
                    continue
                c = berechnen(z or {})
                zeilen.append({"datum": t["datum"], "mid": mid, "name": namen.get(mid, mid), "art": "Einsatz",
                               "wo": e.get("einsatzort") or e.get("kunde") or "", "eid": e["id"],
                               "start_hotel": z.get("start") == "hotel", "ende_hotel": z.get("ende") == "hotel",
                               "offen": not t.get("fertig"), **c})
    einsatz_ort = {e["id"]: e.get("einsatzort", "") for e in speicher.einsaetze()}
    arten = {"anreise": "Anreise", "abreise": "Abreise", "wechsel": "Reise zwischen Jobs"}
    for r in speicher.stamm("reisetage", []):
        if not isinstance(r, dict) or not _im_zeitraum(r.get("datum"), von, bis):
            continue
        a, b = _min(r.get("ab")), _min(r.get("an"))
        dauer = b - a if a is not None and b is not None and b > a else None
        for mid in r.get("ma") or []:
            if wer and mid != wer:
                continue
            zeilen.append({"datum": r["datum"], "mid": mid, "name": namen.get(mid, mid),
                           "art": arten.get(r.get("art"), "Reise"), "wo": einsatz_ort.get(r.get("einsatz"), ""),
                           "eid": r.get("einsatz"), "beginn": r.get("ab"), "ende": r.get("an"), "ruest": 0,
                           "fahrt": dauer or 0, "ort": None, "pause": 0, "gesamt": dauer,
                           "fehlt": [] if dauer else ["Abfahrt/Ankunft"], "offen": False})
    for i in speicher.stamm("intern", []):
        z = (i or {}).get("zeit") or {}
        mid = i.get("erledigt_von") or i.get("zust")
        if not z.get("von") or not _im_zeitraum(z.get("datum"), von, bis) or (wer and mid != wer):
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
                                    "reise": 0, "intern": 0, "offen": 0})
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
        aus.append({"eid": e["id"], "wo": e.get("einsatzort", ""), "k_id": "k:" + k["id"] if k else None,
                    "f_id": "f:" + f["id"] if f else None, "von": anfang, "bis": ende,
                    "status": e.get("status"),
                    "kompressor": f"{k.get('kz', '')} {'' if k.get('eigen') else '(Miete)'}".strip() if k else "",
                    "h_start": st.get("kompH") if k else None, "h_ende": en.get("kompH") if k else None,
                    "h": diff(st.get("kompH"), en.get("kompH")) if k else None,
                    "fahrzeug": f"{f.get('kz', '')} {f.get('name', '')}".strip() if f else "",
                    "km_start": st.get("km"), "km_ende": en.get("km"), "km": diff(st.get("km"), en.get("km"))})
    aus.sort(key=lambda x: x["von"])
    return aus


def tankvorgaenge(von: str, bis: str) -> list[dict]:
    namen = _namen()
    geraet = {"f:" + f["id"]: (f"{f.get('kz', '')} {f.get('name', '')}".strip(), "km")
              for f in speicher.stamm("fahrzeuge", [])}
    geraet.update({"k:" + k["id"]: (f"{k.get('kz', '')} {'Kompressor' if k.get('eigen') else 'Mietkompressor'}", "h")
                   for k in speicher.stamm("kompressoren", [])})
    orte = {e["id"]: e.get("einsatzort", "") for e in speicher.einsaetze()}
    aus = []
    for t in speicher.stamm("tanken", []):
        if not isinstance(t, dict) or not _im_zeitraum(t.get("datum"), von, bis):
            continue
        name, art = geraet.get(t.get("geraet"), (t.get("geraet") or "?", "km"))
        stand = t.get("h") if art == "h" else t.get("km")
        if art == "h" and stand is None and t.get("km") is not None:
            stand = t.get("km")  # ältere Einträge: Stand stand im Kilometerfeld
        aus.append({"datum": t["datum"], "gid": t.get("geraet"), "geraet": name, "art": art, "liter": t.get("l"),
                    "betrag": t.get("b"), "oel_geprueft": bool(t.get("oel_geprueft") or t.get("oel_l")),
                    "oel_l": t.get("oel_l"),
                    "stand": stand, "wer": namen.get(t.get("von"), t.get("von") or ""),
                    "wo": orte.get(t.get("einsatz"), ""), "eid": t.get("einsatz")})
    aus.sort(key=lambda x: (x["datum"], x["geraet"]))
    return aus


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
