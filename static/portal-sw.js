"use strict";
// Scope deliberately stays /static/: no backend route or Service-Worker-Allowed change.
const CACHE = "playroom-portal-shell-v53";
const SHELL = [
  "/static/portal.html",
  "/static/common.css?v=12",
  "/static/portal.css?v=21",
  "/static/common.js?v=12",
  "/static/i18n.js?v=2",
  "/static/auth.js?v=4",
  "/static/push-notifications.js?v=1",
  "/static/portal.js?v=40",
  "/static/portal-match-layout.js?v=3",
  "/static/portal-speed.js?v=1",
  "/static/portal-match-layout.css?v=1",
  "/static/portal-speed.css?v=1",
  "/static/portal-offline.js?v=3",
  "/static/portal.webmanifest",
  "/static/portal-icon.svg",
  "/static/portal-install.js?v=1",
  "/static/portal-icon-180.png",
  "/static/portal-icon-192.png",
  "/static/portal-icon-512.png",
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

// Web push: the server sends {title, body, url, tag} (see push.py), already localized
// for the subscriber. The worker only displays it; the payload carries no credentials.
self.addEventListener("push", (event) => {
  if (!event.data) return;
  let payload;
  try {
    payload = event.data.json();
  } catch {
    return;
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Game platform", {
      body: payload.body || "",
      tag: payload.tag,
      renotify: true,
      icon: "/static/portal-icon.svg",
      data: { url: payload.url || "/static/portal.html" },
    }),
  );
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/static/portal.html";
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        const portal = clients.find((client) =>
          client.url.includes("/static/portal.html"),
        );
        if (portal) return portal.focus().then(() => portal.navigate(url));
        return self.clients.openWindow(url);
      }),
  );
});
