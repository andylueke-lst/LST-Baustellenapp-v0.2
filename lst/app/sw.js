// Hält die App-Oberfläche auf dem iPad vor, damit sie auch ohne Netz startet.
// Daten (Einsätze, Fotos) liegen nicht hier, sondern im Speicher der App.
const CACHE = 'lst-app-v1';
const SHELL = ['/app/', '/app/manifest.webmanifest', '/app/icon-180.png', '/app/icon-512.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  if (u.pathname === '/app' || u.pathname === '/app/' || SHELL.includes(u.pathname)) {
    // Erst das Netz (neueste Version), ohne Netz die gespeicherte
    e.respondWith(fetch(e.request).then(r => { const k = r.clone(); caches.open(CACHE).then(c => c.put('/app/' === u.pathname || u.pathname === '/app' ? '/app/' : e.request, k)); return r; })
      .catch(() => caches.match(u.pathname === '/app' ? '/app/' : e.request)));
  } else if (u.pathname.startsWith('/foto/')) {
    e.respondWith(caches.open('lst-fotos-v1').then(c => c.match(e.request).then(hit => hit || fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }))));
  }
});
