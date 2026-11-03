// Service worker: keeps the app shell available and shows notifications.
const CACHE = 'scorpi-shell-v13';
const SHELL = ['/', '/css/tokens.css', '/css/base.css', '/css/components.css', '/css/shell.css', '/css/chat.css', '/css/pages.css', '/css/canvas.css', '/css/studio.css', '/css/scorpi.css', '/css/radio.css', '/js/main.js', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png'];

// A missing optional file must not stop the worker installing (the app can still start and fetch it).
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.allSettled(SHELL.map((url) => c.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

const STATIC = /^\/(css|js|icons|sounds|fonts)\//;

async function put(request, res) {
  if (!res || !res.ok || res.type === 'opaque') return res;
  const cc = res.headers.get('Cache-Control') || '';
  if (cc.includes('no-store')) return res;
  const copy = res.clone();
  caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
  return res;
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/p/') || url.pathname.startsWith('/f/')) return; // user files and previews
  if (e.request.headers.has('range')) return; // audio/video seeking needs the real response

  // The page itself goes to the network first, so an update is picked up immediately; the cached copy is for offline.
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).then((res) => put(e.request, res)).catch(() => caches.match('/')));
    return;
  }

  // Scripts, styles, icons and sounds: answer from the cache at once and refresh it in the background. A repeat
  // start used to re-download every module before the app could paint, which is what made phones feel slow.
  if (STATIC.test(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then((hit) => {
        const fresh = fetch(e.request).then((res) => put(e.request, res)).catch(() => hit);
        return hit || fresh;
      }),
    );
    return;
  }

  // Anything else (manifest, other assets): network first, cache as an offline fallback.
  e.respondWith(fetch(e.request).then((res) => put(e.request, res)).catch(() => caches.match(e.request)));
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
