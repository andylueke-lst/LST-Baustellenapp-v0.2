# LST Baustelle

Baustellen-App der LST Lüke-Strahl-Technik GmbH: Einsätze, Zeiten, Fotos, Tagesberichte,
Abbau-Check und Lieferschein mit Kundenunterschrift. Läuft als Web-App auf dem Mac mini im
Büro, iPads und Handys greifen über Tailscale zu. Das Konzept liegt im Claude-Projekt
„Baustellen App“.

## Stand

| Stufe | Inhalt | Status |
| --- | --- | --- |
| 1 | Klickbarer Prototyp (Ablauf, Bedienung) | abgestimmt |
| 2 | Dokumente: Lieferschein, Fotodoku, Fotobericht, Tagesbericht | Dokument-Erzeugung fertig, Büro-Freigabe folgt |
| 3 | Geräte, Material, Lohnliste | offen |
| 4 | Offline-Abgleich, Handy-Kamera | offen |
| 5 | Notion-Anbindung (Test-Kopie) | offen |
| 6 | Einrichtung Mac mini | offen |

## Aufbau

```
config/firma.json        Firmendaten, Fußzeile, Schriften, Größengrenzen
assets/                  Briefpapier (Vektor) für Erst- und Folgeseiten
lst/dokumente/           PDF-Erzeugung
  basis.py               Schriften, Briefpapier, Fußzeile, Dateinamen, Bilder
  lieferschein.py        Arbeitsnachweis-Seite + Zusammenfügen mit Lexware
  fotos.py               Fotodokumentation und Fotobericht (≤ 10 MB)
  tagesbericht.py        Tagesbericht mit Unterschrift
  abschluss.py           Erzeugt beim Job-Abschluss alle Dokumente
beispiele/               Skript für Beispiel-Dokumente
prototyp/                Prototyp aus Stufe 1 (eine HTML-Datei)
```

## Dokumente

- **Lieferschein**: Die Lexware-Seiten bleiben unverändert. Hängt eine Seite „Arbeitsnachweis“
  an, wird sie ersetzt. Die App-Seite übernimmt Kopf und Fuß der letzten Lexware-Seite, die
  Seitenzählung wird auf allen Seiten angeglichen.
- **Fotodokumentation**: alle Fotos nach Anlage, Vorher und Nachher. Ihre SHA-256-Prüfsumme
  steht auf dem Lieferschein.
- **Fotobericht**: Auswahl aus dem Büro; wird stufenweise verkleinert, bis er unter 10 MB liegt.
- **Dateinamen**: `JJMMTT Kunde Einsatzort Dokument Nummer.pdf`, z. B.
  `261016 Zimmermann GmbH Zimmermann Lauenau Lieferschein unterschr LS260067.pdf`.
  FD/FB/TB-Nummern übernehmen die Ziffern der LS-Nummer.

## Schriften

Auf dem Mac wird Verdana aus `/System/Library/Fonts/Supplemental/` verwendet, wie in den
Lexware-Belegen. Sonst greift DejaVu Sans.

## Beispiele erzeugen

```
pip install -r requirements.txt
python3 beispiele/erzeuge_beispiele.py <lexware-lieferschein.pdf> <ordner-mit-fotos>
```
