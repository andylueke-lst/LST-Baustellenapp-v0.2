"""Prüft die Übernahme eines Notion-Projekts mit nachgebauten Antworten der Notion-API
(Aufbau wie die echte Projektseite „Epta Aldi Ebersbach/Fils 2026/2“).
Aufruf: LST_DATEN=/tmp/lst-test python3 -m tests.test_notion
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("LST_DATEN", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lst import notion, speicher  # noqa: E402

rt = lambda t: [{"type": "text", "plain_text": t, "text": {"content": t}}]  # noqa: E731
P = "3e4cacaa-22de-8076-bad0-c16ac395b7df"
PROJEKT = {"object": "page", "id": P, "url": "https://www.notion.so/3e4cacaa22de8076bad0c16ac395b7df", "properties": {
    "Projektname": {"type": "title", "title": rt("Epta Aldi Ebersbach/Fils 2026/2")},
    "Umsetzung": {"type": "date", "date": {"start": "2026-11-12", "end": "2026-11-13"}},
    "LST Status externe Projekte": {"type": "status", "status": {"name": "14 Vorbereitung"}},
    "Reinigungsart": {"type": "select", "select": {"name": "Entkalkung"}},
    "2. Mann": {"type": "multi_select", "multi_select": [{"name": "Faro"}]},
    "Bestellnummer": {"type": "rich_text", "rich_text": rt("0011758597")},
    "Kunde": {"type": "relation", "relation": [{"id": "kunde-epta"}]},
    "Einsatz bei": {"type": "relation", "relation": [{"id": "firma-aldi"}]},
    "AP beim Kunden": {"type": "relation", "relation": [{"id": "kontakt-mm"}]},
    "AP Einsatzort": {"type": "relation", "relation": []},
    "Einsatzort": {"type": "rollup", "rollup": {"type": "array", "array": [{"type": "rich_text", "rich_text": rt("Aldi Ebersbach/Fils")}]}},
    "Einsatzstraße": {"type": "rollup", "rollup": {"type": "array", "array": [{"type": "rich_text", "rich_text": rt("Bahnhofstraße 30")}]}},
    "Email Kunde": {"type": "rollup", "rollup": {"type": "array", "array": [{"type": "email", "email": "mm@epta.example"}]}},
    "Email AP Einsatzort": {"type": "rollup", "rollup": {"type": "array", "array": []}},
}}
SEITEN = {
    P: PROJEKT,
    "kunde-epta": {"id": "kunde-epta", "properties": {"Suchname": {"type": "title", "title": rt("Epta")},
                   "Firmenname 1": {"type": "rich_text", "rich_text": rt("Epta Deutschland GmbH")}}},
    "firma-aldi": {"id": "firma-aldi", "properties": {"Suchname": {"type": "title", "title": rt("Aldi Ebersbach")},
                   "Straße": {"type": "rich_text", "rich_text": rt("Bahnhofstraße 30")},
                   "PLZ Straße": {"type": "relation", "relation": [{"id": "plz-73061"}]}}},
    "plz-73061": {"id": "plz-73061", "properties": {"PLZ": {"type": "title", "title": rt("73061")},
                  "Ort": {"type": "rich_text", "rich_text": rt("Ebersbach an der Fils")}}},
    "kontakt-mm": {"id": "kontakt-mm", "properties": {"Name": {"type": "title", "title": rt("Sabine Mühlroth-Marchis")},
                   "E-Mail": {"type": "email", "email": "mm@epta.example"}}},
}
def blk(i, typ, text="", kinder=False, **extra):
    b = {"id": i, "type": typ, "has_children": kinder, typ: {"rich_text": rt(text), **extra}}
    return b
KINDER = {
    P: [blk("leer", "paragraph"), blk("sync", "synced_block", kinder=True, synced_from=None)],
    "sync": [blk("ereig", "heading_1", "Ereignisse", True, is_toggleable=True),
             blk("tech", "heading_1", "Technische Daten", True, is_toggleable=True),
             blk("pinfo", "heading_1", "Projektinfos", True, is_toggleable=True),
             blk("kfm", "synced_block", kinder=True, synced_from={"block_id": "kfm-orig"})],
    "ereig": [blk("e1", "toggle", "Vorlage")],
    "tech": [blk("twt", "heading_2", "Technische Daten Wärmetauscher", True, is_toggleable=True),
             blk("tlas", "heading_2", "Technische Daten Laserreinigung mobil", True, is_toggleable=True)],
    "twt": [blk("twt1", "paragraph", "Technische Daten:\nReinigungsart: Entkalkung\nAnsprechpartner vor Ort: Marktleiter\n"
                "Art der Wärmetauscher: Gaskühler\nAnzahl Wärmetauscher: 2\nStandort Wärmetauscher: Dach\nWasseranschluss:\nAbwasser:")],
    "pinfo": [blk("hotel", "heading_2", "Hotel", True, is_toggleable=True), blk("unt", "heading_2", "Unterlagen", True, is_toggleable=True)],
    "hotel": [], "tlas": [],
    "unt": [blk("info", "toggle", "Informationen vom Kunden"), blk("lsT", "toggle", "Lieferschein", True)],
    "lsT": [{"id": "f1", "type": "file", "has_children": False,
             "file": {"type": "file", "name": "Lieferschein LS260067.pdf", "file": {"url": "https://files.example/ls.pdf"}}}],
    "kfm-orig": [],  # darf nie gelesen werden
}
gelesen = []


def fake(methode, pfad, daten=None):
    gelesen.append((methode, pfad))
    if pfad.endswith("/query"):
        return {"results": [PROJEKT], "has_more": False}
    if pfad.startswith("/pages/"):
        return SEITEN[pfad.split("/")[2]]
    if pfad.startswith("/blocks/"):
        return {"results": KINDER[pfad.split("/")[2]], "has_more": False}
    raise AssertionError(pfad)


def main():
    speicher.einrichten()
    g = speicher.geheim()
    g["notion"] = {"token": "test", "projekte": "644d7ac7-3929-4aea-8f1e-4d9cec143d1a"}
    speicher.pfad("geheim.json").write_text(json.dumps(g))
    notion._anfrage = fake
    lspdf = (Path(__file__).resolve().parents[1] / "beispiele")  # beliebiges PDF reicht nicht für die Nummer
    notion.herunterladen = lambda url: b"%PDF-1.4 test"
    import lst.dokumente.lieferschein as lsmod
    lsmod.lexware_nummer = lambda pdf: "LS260067"
    liste = [notion.kurzinfo(p) for p in notion.projekte_abfragen()]
    assert liste[0]["name"] == "Epta Aldi Ebersbach/Fils 2026/2" and liste[0]["bis"] == "2026-11-13", liste
    e, hinweis = notion.uebernehmen(P)
    print(json.dumps({k: e[k] for k in ("einsatzort", "kunde", "adresse", "apKunde", "apOrt", "von", "bis", "reinigungsart",
                                        "team", "ls")}, ensure_ascii=False, indent=1))
    print("Anlagen:", [a["name"] for a in e["anlagen"]])
    print("Technik:", e["technisch"].replace("\n", " | "))
    print("Hinweis:", hinweis)
    assert e["kunde"] == "Epta Deutschland GmbH"
    assert e["einsatzort"] == "Aldi Ebersbach/Fils"
    assert e["adresse"] == "Bahnhofstraße 30, 73061 Ebersbach an der Fils"
    assert [a["name"] for a in e["anlagen"]] == ["Gaskühler 1", "Gaskühler 2"]
    assert e["team"] == {"vor": "m1", "fach": "m2"} and e["ls"] == "LS260067"
    assert "Wasseranschluss" not in e["technisch"] and "Dach" in e["technisch"]
    assert not any("kfm-orig" in p for _, p in gelesen), "Kaufmännisches wurde gelesen!"
    assert all(m == "GET" or p.endswith("/query") for m, p in gelesen), "Schreibzugriff!"
    assert notion._suche(notion.kinder(P), "Gibt es nicht") is None
    assert not any("kfm-orig" in p for _, p in gelesen), "Kaufmännisches wurde bei erfolgloser Suche gelesen!"
    # zweites Übernehmen aktualisiert, behält Anlagen-IDs
    ids = [a["id"] for a in e["anlagen"]]
    e2, _ = notion.uebernehmen(P)
    assert [a["id"] for a in e2["anlagen"]] == ids and e2["id"] == e["id"]
    assert len([x for x in speicher.einsaetze() if (x.get("notion") or {}).get("id") == P]) == 1
    print("Notion-Test bestanden.")


if __name__ == "__main__":
    main()
