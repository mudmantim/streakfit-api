const CACHE = 'streakfit-v0615';
const STATIC = [
  '/static/style.css',
  '/static/app.js',
  '/static/icons/icon.svg',
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
