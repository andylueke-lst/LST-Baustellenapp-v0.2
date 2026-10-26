# LST Baustelle

Baustellen-App der LST Lüke-Strahl-Technik GmbH: Einsätze, Zeiten, Fotos, Tagesberichte,
Abbau-Check und Lieferschein mit Kundenunterschrift. Läuft als Web-App auf dem Mac mini im
Büro, iPads und Handys greifen über Tailscale zu. Das Konzept liegt im Claude-Projekt
„Baustellen App“.

## Stand

| Stufe | Inhalt | Status |
| --- | --- | --- |
| 1 | Klickbarer Prototyp (Ablauf, Bedienung) | abgestimmt |
| 2 | Dokumente: Lieferschein, Fotodoku, Fotobericht, Tagesbericht | fertig (Dokumente, Server, Büro-Freigabe, Postausgang) |
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
lst/server.py            Web-Server (Flask): Anmeldung, Büro-Seiten, Schnittstelle fürs iPad
lst/speicher.py          SQLite-Datenbank und Dateiablage unter daten/
lst/mail.py              Mailversand mit Postausgang
lst/vorlagen/, statisch/ Seiten und Stil im Notion-Look
lst/demo.py              Legt Testdaten an und spielt einen Job-Abschluss durch
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
  FD/FB/TB-Nummern übernehmen die Ziffern der LS-Nummer. Das Datum ist der Tag der Unterschrift,
  ohne Unterschrift der Tag des Job-Abschlusses – auch für den später freigegebenen Fotobericht.

## Schriften

Auf dem Mac wird Verdana aus `/System/Library/Fonts/Supplemental/` verwendet, wie in den
Lexware-Belegen. Sonst greift DejaVu Sans.

## Beispiele erzeugen

```
pip install -r requirements.txt
python3 beispiele/erzeuge_beispiele.py <lexware-lieferschein.pdf> <ordner-mit-fotos>
```

## Server starten

```
pip install -r requirements.txt
python3 -m lst.demo <lexware-lieferschein.pdf> <ordner-mit-fotos>   # nur zum Testen
python3 -m lst.server                                              # http://localhost:8080
```

Anmeldung mit Name und PIN. Im Test haben alle die PIN 1234, Andy und Tina sehen das Büro.
Daten liegen in `daten/` (änderbar über `LST_DATEN`), Port über `LST_PORT`.

### Büro

- **Freigaben**: abgeschlossene Einsätze, deren Fotobericht noch nicht raus ist.
- **Fotobericht**: Fotos nach Anlage wählen (Vorschlag: erstes Vorher, letztes Nachher),
  Größenanzeige, PDF-Vorschau, „Freigeben und senden“ mit Rückfrage.
- **Postausgang**: Mails, die nicht gesendet werden konnten oder warten, bis der Versand
  eingerichtet ist. „Jetzt senden“ versucht es erneut.
- **Einsatz**: Dokumente öffnen, Lexware-Lieferschein hochladen, Fotodoku nachträglich senden.

### Mailversand einrichten

`daten/geheim.json` (wird beim ersten Start angelegt, nicht im Git) um einen Block ergänzen:

```json
"mail": {"host": "…", "port": 587, "benutzer": "…", "passwort": "…",
         "absender": "LST Lüke-Strahl-Technik <…>", "antwort_an": "post@lueke-strahlt.de",
         "bcc": "post@lueke-strahlt.de", "verbindung": "starttls"}
```

Ohne diesen Block werden alle Mails als `.eml` im Postausgang abgelegt.
