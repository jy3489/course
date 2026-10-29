"use strict";
// Scope deliberately stays /static/: no backend route or Service-Worker-Allowed change.
const CACHE = "playroom-portal-shell-v65";
const SHELL = [
  "/static/portal-feedback.js?v=1",
  "/static/portal.html",
  "/static/common.css?v=12",
  "/static/portal.css?v=27",
  "/static/common.js?v=14",
  "/static/i18n.js?v=2",
  "/static/auth.js?v=6",
  "/static/experiments.js?v=1",
  "/static/experiments.css?v=1",
  "/static/push-notifications.js?v=1",
  "/static/portal.js?v=49",
  "/static/portal-match-layout.js?v=4",
  "/static/portal-speed.js?v=2",
  "/static/portal-match-layout.css?v=1",
  "/static/portal-speed.css?v=2",
  "/static/portal-offline.js?v=5",
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
// An update: delete the older shells' caches, take over open portal pages and reload
// them once, so nobody keeps running the old portal. (A first install reloads nothing.)
self.addEventListener("activate", (event) =>
  event.waitUntil(
    (async () => {
      const old = (await caches.keys()).filter(
        (key) => key.startsWith("playroom-portal-shell-") && key !== CACHE,
      );
      await Promise.all(old.map((key) => caches.delete(key)));
      await self.clients.claim();
      if (!old.length) return;
      const pages = await self.clients.matchAll({ type: "window" });
      await Promise.all(
        pages
          .filter((page) => new URL(page.url).pathname === "/static/portal.html")
          .map((page) => page.navigate(page.url).catch(() => {})),
      );
    })(),
  ),
);
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // Never cache API/auth requests or other students' pages.
  if (
    url.origin !== self.location.origin ||
    !SHELL.includes(url.pathname + url.search) && !/^\/static\/avatars\/(users\/[^/]+|computers)\/[^/]+\.png$/.test(url.pathname)
  )
    return;
  // The page itself comes from the network when online, so a deploy shows up on the
  // next load; the saved copy is the offline fallback. The other files are versioned
  // (?v=N), so their saved copies never go stale.
  if (url.pathname === "/static/portal.html") {
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        try {
          const response = await fetch(event.request, { cache: "no-cache" });
          if (response.ok) await cache.put(event.request, response.clone());
          return response;
        } catch (error) {
          const saved = await cache.match(event.request);
          if (saved) return saved;
          throw error;
        }
      }),
    );
    return;
  }
  event.respondWith(
    caches
      .open(CACHE)
      .then(
        async (cache) =>
          (await cache.match(event.request)) || fetch(event.request).then(response => {
            if (response.ok && url.pathname.startsWith("/static/avatars/")) cache.put(event.request, response.clone());
            return response;
          }),
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
