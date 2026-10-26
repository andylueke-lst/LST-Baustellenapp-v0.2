# Einrichtung am Dienstag

Ziel des Tages: App läuft auf dem Mac mini, das iPad arbeitet damit, und die ersten Einsätze
kommen aus Notion. Notion wird dabei **nur gelesen** – technisch abgesichert, weil die
Integration in Notion gar keine Schreibrechte bekommt.

## 1. Mac mini vorbereiten (ca. 15 Min.)

- [ ] Entscheiden: eigener Benutzer **„LST Server“** mit Firmen-Apple-ID (empfohlen) oder dein Benutzer
- [ ] Systemeinstellungen → Benutzer & Gruppen → **automatisch anmelden** als dieser Benutzer
- [ ] Systemeinstellungen → Energie: Ruhezustand **nie**, „Nach Stromausfall automatisch starten“ an
- [ ] Tailscale installieren, mit **Firmen-Apple-ID** anmelden („E-Mail teilen“ wählen)
- [ ] login.tailscale.com → **DNS**: MagicDNS an, **HTTPS Certificates** an
- [ ] login.tailscale.com → Machines → Mac mini → **Disable key expiry**

## 2. App installieren (ca. 10 Min.)

Am einfachsten: diesen Chat in der Claude-Desktop-App auf dem Mac mini öffnen und einen Ordner
verbinden – dann führe ich die Schritte aus. Sonst im Terminal:

```
git clone https://github.com/andylueke-lst/LST-Baustellenapp-v0.2.git ~/LST-Baustelle
cd ~/LST-Baustelle && ./einrichten.command
```

Das Skript fragt nach den PINs für Andy und Tina, richtet den Autostart und die nächtliche
Sicherung ein (optional Spiegel nach iCloud Drive) und zeigt am Ende die Adresse fürs iPad.

## 3. iPad (ca. 5 Min.)

- [ ] Tailscale installieren, mit derselben Firmen-Apple-ID anmelden
- [ ] Safari → Adresse aus Schritt 2 (`https://<mac-mini>.<tailnet>.ts.net/app/`)
- [ ] Teilen → **Zum Home-Bildschirm**
- [ ] Mit eigenem Namen und PIN anmelden, Kamera-Zugriff erlauben

## 4. Mitarbeiter (ca. 5 Min.)

Büro → **Mitarbeiter**: Nachnamen eintragen (Tina, Matthias, Faro, Arthur), PINs vergeben,
E-Mail für Andy und Tina (Hinweis „Fotobericht zur Freigabe“).

## 5. Notion verbinden (ca. 15 Min.)

1. notion.so/profile/integrations → **Neue Integration**
   - Name: `LST Baustellen-App`, Typ: intern, Workspace: LST
   - Fähigkeiten: **nur „Inhalte lesen“** (Inhalte aktualisieren/einfügen **aus**), Nutzerinfos: keine
2. Schlüssel kopieren und auf dem Mac mini eintragen (wird verdeckt eingegeben):
   `LST_DATEN=~/LST-Baustelle-Daten ~/LST-Baustelle/.venv/bin/python -m lst.verwaltung notion`
3. Zugriff geben – jeweils oben rechts ••• → **Verbindungen** → `LST Baustellen-App`:
   - [ ] LST Projekte
   - [ ] LST Firmenadressen
   - [ ] LST Personen
   - [ ] PLZ-Datenbank (die in den Firmenadressen verknüpft ist)
   - **Nicht** freigeben: LST Projekte kaufmännisch und die übergeordnete Seite „Datenbanken“
4. Büro → **Notion** → ein Projekt übernehmen → Anlagen prüfen → speichern

Hinweis: Mit dem Zugriff auf LST Personen kann die Integration auch Geburtstage lesen. Die App
übernimmt davon nichts außer Name, E-Mail und Telefon.

## 6. Probelauf

- [ ] Testeinsatz „Halle LST“ im Büro anlegen (oder ein übernommenes Projekt)
- [ ] Auf dem iPad: starten, Zeiten, Fotos, Tagesbericht, Abbau-Check, Unterschrift
- [ ] Einmal Flugmodus während der Fotos – Anzeige „Offline · x wartet“, danach überträgt es
- [ ] Im Büro: Lieferschein und Fotodoku öffnen, Fotobericht auswählen, Vorschau

## 7. Wenn Zeit bleibt

- Mailversand: `… -m lst.verwaltung mail` (SMTP-Daten von udmedia)
- Lexware-Lieferschein künftig unter Projektinfos → Unterlagen → Lieferschein ablegen

## Danach (Stufe 3)

Geräte, Material und Lohnliste. Dafür brauche ich ein Beispiel der Stundenliste für den
Steuerberater.
