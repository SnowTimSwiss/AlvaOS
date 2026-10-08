// AlvaOS Hub as an installed app (PWA). Keeps the app itself (not your
// files) so it opens fast and shows a clear message when the NAS is away.
// Everything under /api/, /s/ and /dav goes straight to the NAS.
const SHELL = 'alvaos-hub-v7';
const FILES = ['/', '/demo.js', '/gestures.js', '/app.js', '/app.css', '/calendar.js', '/calendar.css', '/contacts.js', '/contacts.css', '/chat.js', '/chat.css', '/icon.svg',
    '/hub.svg', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(caches.keys()
        .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
        .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'GET' || url.origin !== self.location.origin
        || !FILES.includes(url.pathname)) return;
    // The newest app when the NAS answers, the kept one when it does not.
    event.respondWith(fetch(event.request)
        .then((response) => {
            if (response.ok) {
                const copy = response.clone();
                caches.open(SHELL).then((cache) => cache.put(event.request, copy));
            }
            return response;
        })
        .catch(() => caches.match(event.request)));
});
