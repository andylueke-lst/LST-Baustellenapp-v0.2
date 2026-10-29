"""Baut den Prototyp als eigenständige Web-App für das iPad (offline, ohne Anmeldung).

Ausgabe: web-prototyp/ mit index.html, sw.js, manifest.webmanifest und Symbolen.
Der Ordnerinhalt kommt in ein öffentliches Repository mit GitHub Pages (z. B. lst-prototyp).
Nach dem ersten Öffnen und „Zum Home-Bildschirm“ läuft er ohne Internet; Eingaben bleiben auf dem iPad.

Aufruf: python3 werkzeuge/prototyp_web.py
"""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIEL = ROOT / "web-prototyp"
STAND = datetime.now().strftime("%y%m%d-%H%M")

KOPF = """<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="LST Baustelle">
<meta name="theme-color" content="#ffffff">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icon-180.png">
"""
ANMELDUNG = """<script>
if('serviceWorker' in navigator&&(location.protocol==='https:'||location.hostname==='localhost'))navigator.serviceWorker.register('sw.js').catch(()=>{});
</script>
"""
SW = """// Hält den Prototyp auf dem iPad vor, damit er ohne Internet startet. Stand %s
const CACHE='lst-prototyp-%s';
const DATEIEN=['./','index.html','manifest.webmanifest','icon-180.png','icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(DATEIEN)).then(()=>self.skipWaiting()))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{if(e.request.method!=='GET')return;
  // Erst das Netz (neuester Stand), ohne Netz die gespeicherte Fassung
  e.respondWith(fetch(e.request).then(r=>{if(r.ok){const k=r.clone();caches.open(CACHE).then(c=>c.put(e.request,k))}return r}).catch(()=>caches.match(e.request,{ignoreSearch:true}).then(r=>r||caches.match('index.html'))))});
"""
MANIFEST = """{"name":"LST Baustelle (Erprobung)","short_name":"LST","start_url":"./","scope":"./","display":"standalone",
 "background_color":"#ffffff","theme_color":"#ffffff",
 "icons":[{"src":"icon-180.png","sizes":"180x180","type":"image/png"},{"src":"icon-512.png","sizes":"512x512","type":"image/png"}]}
"""


def bauen():
    s = (ROOT / "prototyp" / "prototyp.html").read_text(encoding="utf-8")
    s = s.replace("<title>", KOPF + "<title>", 1)
    s = s.rstrip() + "\n" + ANMELDUNG
    ZIEL.mkdir(exist_ok=True)
    (ZIEL / "index.html").write_text(s, encoding="utf-8")
    (ZIEL / "sw.js").write_text(SW % (STAND, STAND), encoding="utf-8")
    (ZIEL / "manifest.webmanifest").write_text(MANIFEST, encoding="utf-8")
    for n in ("icon-180.png", "icon-512.png"):
        shutil.copy(ROOT / "lst" / "app" / n, ZIEL / n)
    (ZIEL / "README.md").write_text(
        "# LST Baustelle – Prototyp zur Erprobung\n\nNur erfundene Testdaten. Eingaben bleiben auf dem Gerät.\n"
        "Aus dem privaten Repository erzeugt mit `werkzeuge/prototyp_web.py`.\n", encoding="utf-8")
    print("geschrieben:", ZIEL.relative_to(ROOT), "Stand", STAND)


if __name__ == "__main__":
    bauen()
