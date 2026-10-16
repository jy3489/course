"use strict";
// Scope deliberately stays /static/: no backend route or Service-Worker-Allowed change.
const CACHE = "playroom-portal-shell-v6";
const SHELL = [
  "/static/portal.html",
  "/static/common.css?v=4",
  "/static/common.js?v=2",
  "/static/auth.js?v=1",
  "/static/portal.js?v=5",
  "/static/portal-offline.js?v=1",
  "/static/portal.webmanifest",
  "/static/portal-icon.svg",
];
self.addEventListener("install", (event) =>
  event.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting()),
  ),
);
self.addEventListener("activate", (event) =>
  event.waitUntil(self.clients.claim()),
);
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // Never cache API/auth requests or other students' pages.
  if (
    url.origin !== self.location.origin ||
    !SHELL.includes(url.pathname + url.search)
  )
    return;
  event.respondWith(
    caches
      .open(CACHE)
      .then(
        async (cache) =>
          (await cache.match(event.request)) || fetch(event.request),
      ),
  );
});
