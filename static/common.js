// Helpers shared by the debug pages. Data is always rendered with textContent (never
// innerHTML), since games' code and match states are arbitrary text.

"use strict";

/** Creates an element. [attrs] values go to properties, except "class" and data-*. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("data-") || key.startsWith("aria-")) node.setAttribute(key, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node[key] = value;
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

/** Shows [text] in a banner under the top bar; an empty [text] hides it. */
function showPageError(text) {
  let banner = document.getElementById("page-error");
  if (!banner) {
    banner = el("div", { id: "page-error", class: "page-error" });
    const header = document.querySelector(".topbar");
    if (header) header.after(banner);
    else document.body.prepend(banner);
  }
  banner.textContent = text;
  banner.hidden = !text;
}

// Errors that would otherwise only reach the browser console.
window.addEventListener("error", (event) => showPageError(`Page error: ${event.message}`));
window.addEventListener("unhandledrejection", (event) =>
  showPageError(`Page error: ${event.reason?.message ?? event.reason}`));

async function fetchAllData() {
  const response = await fetch("/debug/all-data");
  if (!response.ok) throw new Error(`GET /debug/all-data: ${response.status}`);
  return response.json();
}

/** Latest version of each game, in creation order. */
function latestGames(allData) {
  const latest = new Map();
  for (const game of allData.game_versions) latest.set(game.id, game);
  return [...latest.values()];
}

function userNames(allData) {
  return new Map(allData.users.map((user) => [user.id, user.display_name]));
}

function shortId(id) {
  return id.length > 8 ? id.slice(0, 8) : id;
}

let toastTimer;
function toast(message) {
  let node = document.getElementById("toast");
  if (!node) {
    node = el("div", { id: "toast", class: "toast" });
    document.body.append(node);
  }
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 1500);
}

/** A short id that shows the full id on hover and copies it on click. */
function idChip(id) {
  return el("span", {
    class: "id",
    title: `${id}\n(click to copy)`,
    textContent: shortId(id),
    onclick: (event) => {
      event.stopPropagation();
      navigator.clipboard.writeText(id).then(() => toast(`Copied ${id}`));
    },
  });
}

/** Shows [text] in full and copies it on click. */
function copyable(text) {
  return el("span", {
    class: "id",
    title: "Click to copy",
    textContent: text,
    onclick: (event) => {
      event.stopPropagation();
      navigator.clipboard.writeText(text).then(() => toast(`Copied ${text}`));
    },
  });
}

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

// API calls

class ApiError extends Error {
  constructor(status, detail) {
    super(ApiError.describe(detail));
    this.status = status;
    this.detail = detail;
  }

  /** FastAPI's 422 details are lists of {loc, msg}; others are strings. */
  static describe(detail) {
    if (Array.isArray(detail)) {
      return detail
        .map((item) => `${(item.loc ?? []).slice(1).join(".") || "body"}: ${item.msg}`)
        .join("; ");
    }
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
}

/** Calls the API as [user] ({id, password} or null); throws ApiError on failure. */
async function apiRequest(method, path, { body, user, signal } = {}) {
  const headers = {};
  if (user) {
    headers["X-User-Id"] = user.id;
    headers["X-User-Password"] = user.password;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const detail = data && data.detail !== undefined ? data.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return data;
}

// "Act as": a select of every user, shared by the builder and portal. The chosen user
// is remembered across pages (the console uses the same key).

const ACTING_USER_KEY = "game-platform.user-id";

/** Fills [select] with [users] and calls [onChange] when the choice changes. */
function setUpActAs(select, users, onChange) {
  const current = select.value || localStorage.getItem(ACTING_USER_KEY);
  select.replaceChildren(
    el("option", { value: "", textContent: "(choose a user)" }),
    ...users.map((user) => el("option", { value: user.id, textContent: user.display_name })),
  );
  select.value = users.some((user) => user.id === current) ? current : "";
  select.onchange = () => {
    localStorage.setItem(ACTING_USER_KEY, select.value);
    onChange();
  };
}

/** The chosen user ({id, display_name, password}), or null. */
function actingUser(select, users) {
  return users.find((user) => user.id === select.value) ?? null;
}

// Hosting a game: its code runs in a sandboxed iframe and talks to the page with
// postMessage. The page sends state_changed; the game sends make_move. See README.md.

/** Runs inside the sandbox. Reflow at the device width first, then shrink only
 * overflowing games. Keep the iframe, scripts, canvas and current state alive. */
function fitGameViewport() {
  const root = document.documentElement;
  let scheduled = false;
  function fit() {
    scheduled = false;
    if (!document.body || !innerWidth || !innerHeight) return;
    root.style.setProperty("zoom", "1", "important");
    let scale = 1;
    for (let pass = 0; pass < 8; pass++) {
      const body = document.body;
      const rect = body.getBoundingClientRect();
      const width = Math.max(rect.right, body.scrollWidth * scale + Math.max(0, rect.left));
      const height = Math.max(rect.bottom, body.scrollHeight * scale + Math.max(0, rect.top));
      const ratio = Math.min(1, innerWidth / Math.max(1, width), innerHeight / Math.max(1, height));
      if (ratio >= .999) break;
      scale *= ratio * .995;
      root.style.setProperty("zoom", String(scale), "important");
    }
  }
  function schedule() {
    if (!scheduled) { scheduled = true; requestAnimationFrame(fit); }
  }
  addEventListener("resize", schedule);
  addEventListener("load", schedule, true); // late images and fonts can change the board size
  new MutationObserver(records => {
    if (records.some(record => record.target !== root)) schedule();
  }).observe(root, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:["style","class","width","height","hidden"]});
  new ResizeObserver(schedule).observe(document.body);
  document.fonts?.ready.then(schedule);
  schedule();
}

class GameFrame {
  /** [onMakeMove({new_state, next_turn_player_indices})] is called for valid make_move messages. */
  constructor(container, onMakeMove, onBadMessage) {
    this.container = container;
    this.onMakeMove = onMakeMove;
    this.onBadMessage = onBadMessage;
    this.iframe = null;
    this.ready = null;
    window.addEventListener("message", (event) => this.receive(event));
    // Builder previews use the height left below their toolbar. Portal matches
    // already allocate the entire available work area through flex layout.
    const resize = () => {
      if (container.id !== "test-frame" || !container.getBoundingClientRect().width) return;
      const work = container.closest(".work");
      const viewportHeight = window.visualViewport?.height || window.innerHeight;
      const available = Math.min(work?.clientHeight || viewportHeight, viewportHeight) -
        (container.getBoundingClientRect().top - (work?.getBoundingClientRect().top || 0) + (work?.scrollTop || 0)) - 20;
      container.style.setProperty("--game-height", Math.max(120, available) + "px");
    };
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(resize).observe(container.closest(".work") || container);
      new ResizeObserver(resize).observe(container);
    }
    window.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("resize", resize);
  }

  /** Loads [code] as the iframe's document. Scripts run, but in an opaque origin, so
   * the game can't reach the platform's cookies, storage or API. */
  load(code) {
    this.iframe = el("iframe", { className: "game-frame", title: "Game" });
    this.iframe.setAttribute("sandbox", "allow-scripts");
    this.ready = new Promise((resolve) => this.iframe.addEventListener("load", resolve, { once: true }));
    this.iframe.srcdoc = code + "<script>(" + fitGameViewport.toString() + ")()<" + "/script>";
    this.container.replaceChildren(this.iframe);
  }

  unload() {
    this.iframe = null;
    this.ready = null;
    this.container.replaceChildren();
  }

  async send(message) {
    const iframe = this.iframe;
    if (!iframe) return;
    await this.ready;
    if (iframe === this.iframe) iframe.contentWindow.postMessage(message, "*");
  }

  receive(event) {
    if (!this.iframe || event.source !== this.iframe.contentWindow) return;
    const message = event.data;
    if (!message || message.type !== "make_move") {
      this.onBadMessage(`ignored a message that isn't make_move: ${JSON.stringify(message)}`);
      return;
    }
    let next = message.next_turn_player_indices;
    // Games written before turn sets send a single index; wrap it into a set.
    if (next === undefined && Number.isInteger(message.next_turn_player_index)) {
      next = [message.next_turn_player_index];
    }
    if (
      !(next === null || (Array.isArray(next) && next.length > 0 && next.every(Number.isInteger))) ||
      !("new_state" in message)
    ) {
      this.onBadMessage(`ignored a malformed make_move: ${JSON.stringify(message)}`);
      return;
    }
    this.onMakeMove({ new_state: message.new_state, next_turn_player_indices: next });
  }
}

/** The state_changed message for a viewer; see README.md for each field. */
function stateChangedMessage({ state, players, turn, status, endReason, moveCount, mySeat, actingFor }) {
  return {
    type: "state_changed",
    state,
    players,
    turn_of_player_indices: turn,
    // Legacy field for games written before turn sets: the first seat in the turn.
    turn_of_player_index: turn === null ? null : turn[0],
    status,
    end_reason: endReason,
    move_count: moveCount,
    my_player_index: mySeat,
    acting_for_player_index: actingFor,
  };
}

function statusClass(status) {
  if (status >= 200 && status < 300) return "ok";
  if (status >= 400 && status < 500) return "warn";
  return "error";
}

/** The same collapsible left drawer on phones, tablets and desktop web. */
function setUpSidebarDrawer() {
  const sidebar=document.querySelector('.builder-shell > .sidebar, .portal-shell > .sidebar');
  if (!sidebar) return;
  const main=document.querySelector('.work'), openers=[];
  let opened=false, returnFocus=null;
  sidebar.id ||= 'app-sidebar';
  const close=el('button',{type:'button',class:'sidebar-close','aria-label':'Close sidebar',textContent:'← Close sidebar',onclick:()=>setOpen(false)});
  sidebar.prepend(close);
  const backdrop=el('button',{type:'button',class:'sidebar-backdrop',tabIndex:-1,'aria-label':'Close sidebar',onclick:()=>setOpen(false)});
  document.body.append(backdrop);
  function setOpen(value,opener) {
    opened=Boolean(value);
    if (opened) returnFocus=opener || document.activeElement;
    document.body.classList.toggle('sidebar-open',opened);
    sidebar.inert=!opened;
    sidebar.setAttribute('aria-hidden',String(!opened));
    sidebar.setAttribute('role','dialog');
    sidebar.setAttribute('aria-label',document.querySelector('.builder-shell')?'Your games':'Your matches');
    sidebar.setAttribute('aria-modal',String(opened));
    main.inert=opened;
    for (const button of openers) button.setAttribute('aria-expanded',String(opened));
    if (opened) close.focus();
    else if (returnFocus) { returnFocus.focus({preventScroll:true}); returnFocus=null; }
  }
  for (const toolbar of [document.querySelector('.topbar'),document.querySelector('.match-toolbar')].filter(Boolean)) {
    const button=el('button',{type:'button',class:'sidebar-toggle',textContent:'☰','aria-label':'Open sidebar','aria-controls':sidebar.id,'aria-expanded':'false'});
    button.addEventListener('click',()=>setOpen(!opened,button));
    toolbar.prepend(button);openers.push(button);
  }
  sidebar.addEventListener('click',event=>{
    if (event.target.closest('.list-item, #new-game, #show-creator')) setOpen(false);
  });
  document.addEventListener('keydown',event=>{
    if (!opened) return;
    if (event.key==='Escape') {event.preventDefault();setOpen(false);}
    if (event.key==='Tab') {
      const controls=[...sidebar.querySelectorAll('button:not([disabled]),a[href],summary,input:not([disabled]),select,textarea,[tabindex="0"]')].filter(node=>node.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if (event.shiftKey && document.activeElement===first) {event.preventDefault();last?.focus();}
      else if (!event.shiftKey && document.activeElement===last) {event.preventDefault();first?.focus();}
    }
  });
  setOpen(false);
}
if (document.readyState==='complete'||document.readyState==='interactive') setUpSidebarDrawer();
else document.addEventListener?.('DOMContentLoaded',setUpSidebarDrawer,{once:true});
