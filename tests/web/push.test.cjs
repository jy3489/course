// Portal web push checks: the notifications toggle in portal.js, run under a small
// DOM/Push API stub (no browser, no push service). See tests/web/portal.test.cjs.

const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

// A valid-looking VAPID public key: base64url of 65 bytes.
const PUBLIC_KEY = "B" + "E".repeat(87);

function browserSubscription(endpoint, registry) {
  return {
    endpoint,
    expirationTime: null,
    keys: { p256dh: "browser-key", auth: "browser-auth" },
    toJSON() {
      return {
        endpoint: this.endpoint,
        expirationTime: this.expirationTime,
        keys: this.keys,
      };
    },
    async unsubscribe() {
      registry._sub = null;
    },
  };
}

function setup({
  config = { enabled: true, vapid_public_key: PUBLIC_KEY },
  subscription = null,
  permission = "default",
} = {}) {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id))
      nodes.set(id, {
        value: "",
        hidden: false,
        disabled: false,
        textContent: "",
        dataset: {},
        children: [],
        replaceChildren(...children) {
          this.children = children;
        },
        setAttribute() {},
        addEventListener() {},
        focus() {},
      });
    return nodes.get(id);
  };
  const fetches = [];
  const registration = {
    active: { state: "activated" },
    pushManager: {
      _sub: null,
      subscribeCalls: [],
      async getSubscription() {
        return this._sub;
      },
      async subscribe(options) {
        this.subscribeCalls.push(options);
        this._sub = browserSubscription("https://push.example.com/new", this);
        return this._sub;
      },
    },
  };
  if (subscription)
    registration.pushManager._sub = browserSubscription(
      subscription,
      registration.pushManager,
    );
  const NotificationStub = {
    permission,
    async requestPermission() {
      this.permission = "granted";
      return "granted";
    },
  };
  const fetch = async (url, opts = {}) => {
    fetches.push([
      opts.method || "GET",
      url,
      opts.body ? JSON.parse(opts.body) : undefined,
      opts.headers || {},
    ]);
    if (url === "/push/config")
      return { ok: true, text: async () => JSON.stringify(config) };
    return { ok: true, text: async () => "{}" };
  };
  class AbortControllerStub {
    constructor() {
      this.signal = {};
    }
    abort() {}
  }
  const ctx = vm.createContext({
    console, Map, Set, URLSearchParams, Promise, JSON, Math, Date, Number, String,
    Boolean, Array, Object, Uint8Array, atob, encodeURIComponent, setTimeout,
    clearTimeout, AbortController: AbortControllerStub, fetch,
    navigator: {
      onLine: true,
      serviceWorker: {
        register: async () => registration,
        getRegistration: async () => registration,
      },
    },
    localStorage: {},
    Notification: NotificationStub,
    PortalOffline: class {
      api(...args) {
        return fetch(...args);
      }
      read() {
        return {};
      }
      write() {}
    },
    document: { getElementById: node, addEventListener() {} },
    window: {
      addEventListener() {},
      PushManager: {},
      Notification: NotificationStub,
    },
    location: { hash: "" },
    history: { replaceState() {} },
    Auth: { current: () => ({ id: "owner", password: "pw" }), renderMenu() {} },
    GameFrame: class {
      load() {}
      unload() {}
      send() {}
    },
    apiRequest: fetch,
    formatTime: () => "",
    shortId: (x) => x,
    showPageError() {},
    el: (tag, attrs = {}, ...children) => ({ ...attrs, children }),
  });
  const load = (file) =>
    fs.readFileSync(path.join(__dirname, "../../static", file), "utf8");
  vm.runInContext(load("push-notifications.js"), ctx);
  vm.runInContext(load("portal.js").replace(/main\(\);\s*$/, ""), ctx);
  return {
    node,
    registration,
    fetches,
    run: (code) => vm.runInContext(code, ctx),
  };
}

test("enabling notifications subscribes the browser and posts the subscription", async () => {
  const { node, registration, fetches, run } = setup();
  await run("setupNotifications()");
  assert.equal(node("notify-toggle").hidden, false);
  assert.equal(node("notify-toggle").textContent, "Enable notifications");

  await run("toggleNotifications()");
  const subscribe = registration.pushManager.subscribeCalls[0];
  assert.equal(subscribe.userVisibleOnly, true);
  assert(subscribe.applicationServerKey instanceof Uint8Array);
  const post = fetches.find((f) => f[0] === "POST" && f[1] === "/push/subscriptions");
  assert(post, "the subscription was posted");
  assert.deepEqual(post[2], {
    endpoint: "https://push.example.com/new",
    keys: { p256dh: "browser-key", auth: "browser-auth" },
    expiration_time: null,
  });
  assert.equal(post[3]["X-User-Id"], "owner");
  assert.equal(post[3]["X-User-Password"], "pw");
  assert.equal(node("notify-toggle").textContent, "Notifications on");
});

test("disabling notifications deletes by endpoint and unsubscribes the browser", async () => {
  const { node, fetches, run } = setup({
    subscription: "https://push.example.com/existing",
    permission: "granted",
  });
  await run("setupNotifications()");
  fetches.length = 0;

  await run("toggleNotifications()");
  const del = fetches.find((f) => f[0] === "DELETE");
  assert.equal(
    del[1],
    "/push/subscriptions?endpoint=" +
      encodeURIComponent("https://push.example.com/existing"),
  );
  assert.equal(del[3]["X-User-Id"], "owner");
  assert.equal(await run("currentPushSubscription()"), null);
  assert.equal(node("notify-toggle").textContent, "Enable notifications");
});

test("a live subscription is re-registered as the current account on load", async () => {
  const { fetches, run } = setup({
    subscription: "https://push.example.com/existing",
    permission: "granted",
  });
  await run("setupNotifications()");
  const post = fetches.find((f) => f[0] === "POST" && f[1] === "/push/subscriptions");
  assert(post, "the existing subscription was re-posted");
  assert.equal(post[2].endpoint, "https://push.example.com/existing");
});

test("the toggle stays hidden when the server has push disabled", async () => {
  const { node, run } = setup({ config: { enabled: false, vapid_public_key: "" } });
  await run("setupNotifications()");
  assert.equal(node("notify-toggle").hidden, true);
});

test("a denied browser permission shows the blocked state", async () => {
  const { node, run } = setup({ permission: "denied" });
  await run("setupNotifications()");
  assert.equal(node("notify-toggle").hidden, false);
  assert.equal(
    node("notify-toggle").textContent,
    "Notifications blocked by the browser",
  );
  assert.equal(node("notify-toggle").disabled, true);
});

