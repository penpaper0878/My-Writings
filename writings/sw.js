/* =============================================================================
   sw.js — the offline cache.

   Everything is network-first. When the connection works, the reader gets the
   current code and the current writing, always; the cache is only a fallback
   for when it does not. (Serving cached code first looks faster, but it means
   a fix keeps not running until the second visit after it ships.)

   Two rules learned the hard way:

   * Never invent a response. This worker used to answer a failed request for
     the content file with an empty "{}", which the page took for a real, empty
     site — and an admin who then published wrote that emptiness over their
     actual writing. A failed request now fails, visibly.

   * Cache the content file under one stable key. The page asks for it with a
     cache-busting ?v=…, and caching each of those separately meant an offline
     fallback that could never match, plus a cache that grew on every visit.
   ========================================================================== */

var CACHE_PREFIX = 'writings-';
// Bump this whenever the app's own files change, so old caches are cleared.
var CACHE = CACHE_PREFIX + 'v3';

// How long to wait on the network before falling back to a cached copy.
var NETWORK_TIMEOUT_MS = 4000;

var SHELL = [
  './',
  './index.html',
  './manifest.json',
  './assets/css/app.css',
  './assets/js/store.js',
  './assets/js/i18n.js',
  './assets/js/theme.js',
  './assets/js/ui.js',
  './assets/js/views.js',
  './assets/js/share.js',
  './assets/js/publish.js',
  './assets/js/editor.js',
  './assets/js/settings.js',
  './assets/js/feedback.js',
  './assets/js/app.js',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-512-maskable.png',
  './icons/apple-touch-icon.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      // One missing file must not stop the whole worker installing.
      return Promise.all(SHELL.map(function (url) {
        return cache.add(url).catch(function () { return null; });
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        // Only our own old versions. Instatements at the site root keeps a
        // cache of its own on this same origin.
        if (key.indexOf(CACHE_PREFIX) !== 0 || key === CACHE) return null;
        return caches.delete(key);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

/* The same address without its query string, so ?v=123 and ?v=456 share one
   cache entry. */
function stableKey(url) {
  return url.origin + url.pathname;
}

function networkFirst(request, key) {
  return new Promise(function (resolve) {
    var settled = false;
    function settle(response) {
      if (settled || !response) return false;
      settled = true;
      resolve(response);
      return true;
    }

    // A slow connection should not leave the reader staring at nothing when a
    // good copy is already here.
    var timer = setTimeout(function () {
      caches.match(key).then(settle);
    }, NETWORK_TIMEOUT_MS);

    fetch(request).then(function (response) {
      clearTimeout(timer);
      if (response && response.ok) {
        var copy = response.clone();
        caches.open(CACHE).then(function (cache) { cache.put(key, copy); });
      }
      settle(response);
    }).catch(function () {
      clearTimeout(timer);
      caches.match(key).then(function (cached) {
        if (settle(cached)) return;
        if (request.mode === 'navigate') {
          caches.match('./index.html').then(function (shell) {
            settle(shell || new Response('Offline', { status: 503, statusText: 'Offline' }));
          });
          return;
        }
        // Say plainly that it failed. Never make something up.
        settle(new Response('', { status: 503, statusText: 'Offline' }));
      });
    });
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;

  var url = new URL(request.url);
  // Google Fonts and the GitHub API go straight to the network, uncached.
  if (url.origin !== self.location.origin) return;
  // Only this site's own folder; Instatements at the root has its own worker.
  var scopePath = new URL(self.registration.scope).pathname;
  if (url.pathname.indexOf(scopePath) !== 0) return;

  event.respondWith(networkFirst(request, stableKey(url)));
});
