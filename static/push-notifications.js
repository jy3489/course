// Web push notifications, shared by the portal and the builder: the toggle
// subscribes this browser (Push API) and registers the subscription with the
// server, which then pushes turn and match events (see push.py). Arriving pushes
// are shown by portal-sw.js. Everything is opt-in: nothing here runs unless the
// server has push configured (GET /push/config) and the user enables it.

"use strict";

const PushNotifications = (() => {
  let config = null; // GET /push/config, fetched once per page load.

  function supported() {
    return (
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window
    );
  }

  // The portal's service worker owns push; its scope is /static/. Registering it
  // from any page returns that same registration (a page outside the scope, like
  // /builder, isn't controlled by it, but push doesn't need that).
  // navigator.serviceWorker.ready would hang on such a page, so wait for
  // activation explicitly.
  async function registrationForPush() {
    await navigator.serviceWorker.register("/static/portal-sw.js");
    const registration = await navigator.serviceWorker.getRegistration(
      "/static/portal.html",
    );
    if (!registration || registration.active) return registration;
    const worker = registration.installing || registration.waiting;
    if (!worker) return null;
    await new Promise((resolve) => {
      worker.addEventListener("statechange", () => {
        if (worker.state === "activated" || worker.state === "redundant") resolve();
      });
    });
    return registration.active ? registration : null;
  }

  /** This browser's current push subscription, or null. */
  async function currentSubscription() {
    try {
      const registration = await registrationForPush();
      return registration ? await registration.pushManager.getSubscription() : null;
    } catch {
      return null;
    }
  }

  function urlBase64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (char) => char.charCodeAt(0));
  }

  /** Registers (or refreshes) the browser's subscription server-side. [transport]
   * is the page's API caller: (method, path, {body, user}). */
  async function post(transport, subscription) {
    const keys = subscription.toJSON().keys;
    await transport("POST", "/push/subscriptions", {
      user: Auth.current(),
      body: {
        endpoint: subscription.endpoint,
        keys: { p256dh: keys.p256dh, auth: keys.auth },
        expiration_time: subscription.expirationTime
          ? new Date(subscription.expirationTime).toISOString()
          : null,
      },
    });
  }

  async function updateToggle(button) {
    if (!supported() || !Auth.current() || !config?.enabled) {
      button.hidden = true;
      return;
    }
    button.hidden = false;
    if (Notification.permission === "denied") {
      button.textContent = "Notifications blocked by the browser";
      button.disabled = true;
      return;
    }
    button.disabled = false;
    const subscribed = Boolean(await currentSubscription());
    button.textContent = subscribed ? "Notifications on" : "Enable notifications";
    button.setAttribute("aria-pressed", String(subscribed));
  }

  async function toggle(button, transport) {
    button.disabled = true;
    try {
      const existing = await currentSubscription();
      if (existing) {
        await transport(
          "DELETE",
          `/push/subscriptions?endpoint=${encodeURIComponent(existing.endpoint)}`,
          { user: Auth.current() },
        );
        await existing.unsubscribe();
      } else {
        const permission = await Notification.requestPermission();
        if (permission === "granted") {
          const registration = await registrationForPush();
          const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(config.vapid_public_key),
          });
          await post(transport, subscription);
        }
      }
    } catch (error) {
      showPageError(`Notifications: ${error.message}`);
    } finally {
      button.disabled = false;
      await updateToggle(button);
    }
  }

  /** Shows the toggle when push is available, and re-registers a live subscription
   * as the current account: server restarts, VAPID key rotations and account
   * switches heal themselves on load. Never throws. */
  async function setup(button, transport) {
    if (!supported()) return;
    try {
      config ??= await transport("GET", "/push/config");
    } catch {
      return; // Push unreachable; the toggle stays hidden.
    }
    try {
      const subscription = await currentSubscription();
      if (subscription && Auth.current() && Notification.permission === "granted")
        await post(transport, subscription);
    } catch {
      // Offline: the next load retries.
    }
    await updateToggle(button);
  }

  return { supported, currentSubscription, setup, toggle, updateToggle };
})();
