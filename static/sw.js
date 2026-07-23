const CACHE = 'streakfit-v0743';
const STATIC = [
  '/static/style.css',
  '/static/app.js',
  '/static/icons/icon.svg',
  '/static/icons/icon-180.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(STATIC); })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) { return k !== CACHE; })
            .map(function (k) { return caches.delete(k); })
      );
    }).then(function () { return clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  var url = new URL(e.request.url);

  // Always go to network for API and HTML — never serve stale auth/data
  if (url.pathname.startsWith('/api/') || url.pathname === '/') {
    e.respondWith(fetch(e.request));
    return;
  }

  // Cache-first for static assets, fall back to network
  e.respondWith(
    caches.match(e.request).then(function (cached) {
      return cached || fetch(e.request).then(function (response) {
        if (response.ok) {
          var clone = response.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, clone); });
        }
        return response;
      });
    })
  );
});

// ── Push Notifications ─────────────────────────────────────────────────────────

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var destUrl = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if (c.url.indexOf(self.location.origin) === 0 && 'focus' in c) {
          return c.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(destUrl);
    })
  );
});
