const CACHE_NAME = "ruralcare-v1.0.0";
const STATIC_ASSETS = [
  "/",
  "/static/css/style.css",
  "/static/js/i18n/translations.js",
  "/static/js/i18n/i18n.js",
  "/static/js/offline_db.js",
  "/static/js/speech.js",
  "/static/js/app.js",
  "/manifest.json",
  "/static/manifest.json",
  "/static/icons/icon-192.svg",
  "/static/icons/icon-512.svg"
];

// Install event: cache core application shell
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log("[ServiceWorker] Caching app shell & static assets");
      return cache.addAll(STATIC_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// Activate event: clean up outdated caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log("[ServiceWorker] Removing old cache:", key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event: Network-first for dynamic API, Cache-first with network fallback for assets
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // API calls are handled with network-first; client manages offline queue in IndexedDB
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() => {
        // Return friendly offline JSON response for API calls if server unreachable
        return new Response(
          JSON.stringify({
            success: false,
            offline: true,
            error: "Device is currently offline. Action stored locally for background sync."
          }),
          { headers: { "Content-Type": "application/json" } }
        );
      })
    );
    return;
  }

  // Static shell & pages: Stale-while-revalidate / cache-first
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        // Fetch in background to update cache
        fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, networkResponse);
            });
          }
        }).catch(() => {/* Offline */});
        return cachedResponse;
      }
      return fetch(event.request).then((networkResponse) => {
        return networkResponse;
      }).catch(() => {
        // If navigation request fails offline, fallback to cached root index.html
        if (event.request.mode === "navigate") {
          return caches.match("/");
        }
      });
    })
  );
});
