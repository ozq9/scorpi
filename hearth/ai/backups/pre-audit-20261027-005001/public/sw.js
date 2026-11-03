// Service worker: keeps the app shell available and shows notifications.
const CACHE = 'scorp-shell-v11';
const SHELL = ['/', '/css/tokens.css', '/css/base.css', '/css/components.css', '/css/shell.css', '/css/chat.css', '/css/pages.css', '/css/canvas.css', '/css/studio.css', '/css/scorpi.css', '/css/radio.css', '/js/main.js', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // network first so updates show immediately; cache as an offline fallback
  e.respondWith(
    // one retry: over a proxy a burst of requests can lose one, and a script that is missing stops the whole app starting
    fetch(e.request)
      .catch(() => fetch(e.request))
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      // the page itself may fall back to the cached page; a script or stylesheet must never be answered with a page
      .catch(() => caches.match(e.request).then((hit) => hit || (e.request.mode === 'navigate' ? caches.match('/') : Response.error()))),
  );
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const link = (e.notification.data && e.notification.data.link) || '/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((wins) => {
      for (const w of wins) {
        if ('focus' in w) {
          w.navigate(link);
          return w.focus();
        }
      }
      return self.clients.openWindow(link);
    }),
  );
});
