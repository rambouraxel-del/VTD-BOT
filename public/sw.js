// Service worker minimal : met en cache l'app pour un usage hors ligne.
const CACHE = 'vtd-deals-v2'

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(['./', './manifest.webmanifest'])))
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)
  // Jamais de cache pour l'API (données à jour, clé d'accès).
  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.includes('/api/')) return
  // Réseau d'abord, cache en secours.
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone()
        caches.open(CACHE).then((c) => c.put(req, copy))
        return res
      })
      .catch(() => caches.match(req).then((r) => r || caches.match('./'))),
  )
})
