"""Erzeugt eine Einsatz-Datei für den Prototyp (Einsatz + Lexware-Lieferschein als Seitenbilder).

Aufruf: python3 werkzeuge/einsatz_datei.py <code.txt oder einsatz.json> <lexware.pdf> <ziel.json>
Die Datei wird auf dem iPad unter „＋ Einsatz einfügen“ → „Einsatz-Datei wählen“ geöffnet.
Sie enthält Kundendaten: nur direkt weitergeben, nie ins öffentliche Repository legen.
"""
import base64
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lst.dokumente.lieferschein import lexware_nummer  # noqa: E402


def einsatz_lesen(p: Path) -> dict:
    t = p.read_text(encoding="utf-8").strip()
    if t.startswith("LST1:"):
        return json.loads(base64.b64decode(t[5:]))["einsatz"]
    d = json.loads(t)
    return d.get("einsatz", d)


def seiten(pdf: Path, breite=1400) -> list[str]:
    from pypdf import PdfReader
    texte = [(s.extract_text() or "") for s in PdfReader(str(pdf)).pages]
    # Eine alte Seite „Arbeitsnachweis“ am Ende weglassen – die erzeugt die App selbst
    n = len(texte) - 1 if len(texte) > 1 and "Arbeitsnachweis" in texte[-1] else len(texte)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "170", "-l", str(n), "-png", str(pdf), f"{tmp}/s"], check=True)
        aus = []
        for png in sorted(Path(tmp).glob("s*.png")):
            im = Image.open(png).convert("RGB")
            if im.width > breite:
                im = im.resize((breite, round(im.height * breite / im.width)), Image.LANCZOS)
            b = io.BytesIO()
            im.save(b, "JPEG", quality=72, optimize=True)
            aus.append("data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode())
        return aus


def main():
    einsatz = einsatz_lesen(Path(sys.argv[1]))
    pdf = Path(sys.argv[2])
    nr = lexware_nummer(pdf.read_bytes())
    if nr:
        einsatz["ls"] = nr
    d = {"typ": "einsatz", "einsatz": einsatz, "lsSeiten": seiten(pdf)}
    Path(sys.argv[3]).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"{sys.argv[3]}: {einsatz['einsatzort']}, {nr or 'ohne LS-Nummer'}, {len(d['lsSeiten'])} Seiten,"
          f" {Path(sys.argv[3]).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
