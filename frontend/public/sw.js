// BSNexus Service Worker — first cut for the mobile web/PWA wedge
// (decision-locks O2, 2026-05-08).
//
// Goals:
// 1. Make the app installable on iOS / Android.
// 2. Don't break offline navigation: a one-shot HTML fallback so a cold
//    install doesn't show "no internet" before the network revives.
// 3. Never cache mutating requests (POST/PATCH/DELETE). API GETs go
//    network-first with a tiny stale-while-revalidate fallback so the
//    Brief surface paints something instead of a blank screen on a flaky
//    LTE connection.
//
// We intentionally don't pre-cache the JS / CSS bundles — Next.js
// hashes them, so a stale SW would fight an in-flight deploy. Bundles
// are HTTP-cached by the CDN and revalidated by the browser the normal
// way.

const SHELL_CACHE = 'bsnexus-shell-v1'
const RUNTIME_CACHE = 'bsnexus-runtime-v1'
const OFFLINE_FALLBACK = '/'

self.addEventListener('install', (event) => {
  // Pre-warm the offline fallback so the app's shell HTML is reachable
  // when the user adds-to-home and opens it without connectivity.
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.add(OFFLINE_FALLBACK))
      .catch(() => undefined),
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== RUNTIME_CACHE)
            .map((k) => caches.delete(k)),
        ),
      ),
  )
  self.clients.claim()
})

function isMutatingRequest(request) {
  if (request.method !== 'GET') return true
  // SSE stream — never cache, the browser must keep the live connection.
  if (request.headers.get('accept') === 'text/event-stream') return true
  return false
}

function isApiRequest(url) {
  return url.pathname.startsWith('/api/')
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)

  // Cross-origin requests pass through untouched (auth callbacks, fonts
  // come from next/font self-hosted, etc.).
  if (url.origin !== self.location.origin) {
    return
  }

  if (isMutatingRequest(request)) {
    // Mutations and SSE: always network. Never cached, never queued.
    return
  }

  if (request.mode === 'navigate') {
    // App shell: try network first; fall back to the cached root HTML
    // so an offline cold-open still mounts the SPA.
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_FALLBACK).then((cached) =>
          cached ?? new Response('offline', { status: 503, statusText: 'offline' }),
        ),
      ),
    )
    return
  }

  if (isApiRequest(url)) {
    // API GETs: stale-while-revalidate. The Brief / Deliverables /
    // Decisions surfaces paint immediately on the cached snapshot while
    // the live request updates the cache for next time.
    event.respondWith(
      caches.open(RUNTIME_CACHE).then(async (cache) => {
        const cached = await cache.match(request)
        const networkPromise = fetch(request)
          .then((response) => {
            if (response.ok) {
              cache.put(request, response.clone()).catch(() => undefined)
            }
            return response
          })
          .catch(() => cached)
        return cached ?? networkPromise
      }),
    )
    return
  }

  // Static asset (Next.js bundle, image, etc.): default browser HTTP
  // caching is fine. Don't intercept.
})
