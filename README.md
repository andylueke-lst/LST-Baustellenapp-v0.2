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
| 2b | iPad-App am Server: Anmeldung, Abgleich, Offline-Zwischenspeicher | fertig, Probelauf auf dem Mac mini offen |
| 3 | Geräte, Material, Lohnliste | offen |
| 4 | Offline-Abgleich, Handy-Kamera | offen |
| 5 | Notion-Anbindung | Lesen fertig (Projekte übernehmen), Schreiben offen |
| 6 | Einrichtung Mac mini | Skript fertig, Einrichtung am Dienstag |

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
lst/app/                 iPad-App (wird aus prototyp/ erzeugt: python3 werkzeuge/app_bauen.py)
lst/notion.py            Projekte aus Notion lesen (nur lesend)
lst/sicherung.py         Nächtliche Sicherung, optional Spiegel nach iCloud
lst/verwaltung.py        PIN setzen, Mitarbeiterliste, Adresse anzeigen
einrichten.command       Einrichtung auf dem Mac mini (Doppelklick)
aktualisieren.command    Neue Version holen und Server neu starten
tests/                   Prüfungen (z. B. Notion-Übernahme mit nachgebauter API)
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

## iPad-App

`/app/` ist die App für iPad und Handy (in Safari öffnen, „Zum Home-Bildschirm“). Bedienung wie
im abgestimmten Prototyp. Alles wird zuerst auf dem Gerät gespeichert und im Hintergrund zum
Mac mini übertragen; oben rechts steht der Stand („Alles im Büro“, „Offline · 2 wartet“).

- Anmeldung mit Name und PIN; das iPad bleibt 60 Tage angemeldet. Ohne Netz geht die Anmeldung,
  wenn sich die Person auf diesem iPad schon einmal angemeldet hat.
- Einsätze kommen vom Büro (angelegt unter „＋ Einsatz“ oder aus Notion übernommen).
- Fotos gehen einzeln hoch, sobald Netz da ist. Der Job-Abschluss wartet, bis alle Fotos
  angekommen sind, dann erzeugt der Mac mini Lieferschein und Fotodoku.
- Was das Büro entscheidet (Status abgeschlossen, Lexware-Nummer), überschreibt das iPad nicht.

Änderungen an der Oberfläche werden im Prototyp gemacht und mit `python3 werkzeuge/app_bauen.py`
in die App übernommen.

## Notion

Die App liest Projekte aus „LST Projekte“ (Status 13–15, letzte 7 bis nächste 60 Tage) und legt
daraus vorbereitete Einsätze an: Einsatzort, Kunde, Adresse, Ansprechpartner, Termin,
Reinigungsart, 2. Mann, technische Daten, Anlagen-Vorschlag und – falls vorhanden – den
Lexware-Lieferschein aus Projektinfos → Unterlagen → Lieferschein.

Zugang in `geheim.json`:

```json
"notion": {"token": "ntn_…", "projekte": "644d7ac7-3929-4aea-8f1e-4d9cec143d1a", "schreiben": false}
```

Die Integration bekommt in Notion nur die Fähigkeit „Inhalte lesen“ und nur Zugriff auf
LST Projekte, LST Firmenadressen, die Kontakte und die PLZ-Datenbank – nie auf
„LST Projekte kaufmännisch“ und nie auf die übergeordnete Seite „Datenbanken“. Verweise auf
synchronisierte Blöcke (der Abschnitt „Kaufmännisches“) öffnet die App grundsätzlich nicht.

Prüfung ohne Netz: `LST_DATEN=$(mktemp -d) python3 -m tests.test_notion`
