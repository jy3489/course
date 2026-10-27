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

/** Measure inside the sandbox, but scale the iframe outside it. CSS zoom changes
 * the game's own layout/measurements and can feed back into its resize handlers. */
function fitGameViewport() {
  const root = document.documentElement;
  let scheduled = false;
  let previous = "";
  function fit() {
    scheduled = false;
    if (!document.body || !innerWidth || !innerHeight) return;
    const body = document.body, rect = body.getBoundingClientRect(), style = getComputedStyle(body);
    const width = Math.ceil(Math.max(rect.right, body.scrollWidth + rect.left) + (parseFloat(style.marginRight) || 0));
    const height = Math.ceil(Math.max(rect.bottom, body.scrollHeight + rect.top) + (parseFloat(style.marginBottom) || 0));
    const key = [width, height, innerWidth, innerHeight].join(",");
    if (key === previous) return;
    previous = key;
    parent.postMessage({type:"game_viewport", width, height, viewport_width:innerWidth, viewport_height:innerHeight}, "*");
  }
  function schedule() {
    if (!scheduled) { scheduled = true; requestAnimationFrame(fit); }
  }
  addEventListener("resize", schedule);
  addEventListener("load", schedule, true); // late images and fonts can change the board size
  new MutationObserver(schedule).observe(root, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:["style","class","width","height","hidden"]});
  new ResizeObserver(schedule).observe(document.body);
  document.fonts?.ready.then(schedule);
  schedule();
}

class GameFrame {
  /** [onMakeMove] is called for valid make_move messages: ({new_state,
   * next_turn_player_indices}) for a v1 game, ({move}) for a v2 game whose
   * server code validates and applies the move. */
  constructor(container, onMakeMove, onBadMessage) {
    this.container = container;
    this.onMakeMove = onMakeMove;
    this.onBadMessage = onBadMessage;
    this.iframe = null;
    this.ready = null;
    this.viewport = null;
    window.addEventListener("message", (event) => this.receive(event));
    // Inline Builder previews scroll with the workspace. Do not squeeze them
    // into the space left below the toolbar: on phones that can be only 120px.
    // Defer observed size changes so fitting never writes during delivery.
    let pending = false;
    const resize = () => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; this.resizeViewport(); });
    };
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(resize).observe(container);
    }
    window.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("resize", resize);
  }

  /** Loads [code] as the iframe's document. Scripts run, but in an opaque origin, so
   * the game can't reach the platform's cookies, storage or API. */
  load(code) {
    this.viewport = null;
    this.iframe = el("iframe", { className: "game-frame", title: "Game" });
    this.iframe.setAttribute("sandbox", "allow-scripts");
    this.ready = new Promise((resolve) => this.iframe.addEventListener("load", resolve, { once: true }));
    this.iframe.srcdoc = code + "<script>(" + fitGameViewport.toString() + ")()<" + "/script>";
    this.container.replaceChildren(this.iframe);
    this.resizeViewport();
  }

  resizeViewport() {
    if (!this.iframe) return;
    const width = this.container.clientWidth;
    const autoHeight = this.container.id === "test-frame" && !document.body.classList.contains("builder-preview-focus");
    // A new width/game or leaving Focus gets a fresh natural-height measurement.
    if (autoHeight && (this.viewport?.width !== width || !this.viewport?.autoHeight)) {
      this.container.style.removeProperty("--game-height");
    }
    const height = this.container.clientHeight;
    if (!width || !height) return; // a closed test panel will be measured when shown
    if (this.viewport?.width === width && this.viewport?.height === height && this.viewport?.autoHeight === autoHeight) return;
    if (autoHeight && this.viewport?.autoHeight && this.viewport.width === width) {
      this.viewport.height = height;
    } else this.viewport = {width, height, scale:1, passes:0, autoHeight};
    this.applyViewport();
  }

  applyViewport() {
    const {width, height, scale} = this.viewport;
    // Keep the layout viewport and visual viewport proportional. Responsive
    // boards reflow normally; transforming their iframe cannot change their DOM
    // measurements or canvas coordinates. Never reload a game to resize it.
    Object.assign(this.iframe.style, {width:Math.ceil(width / scale)+"px", height:Math.ceil(height / scale)+"px",
      minHeight:"0", maxWidth:"none", flex:"none", transformOrigin:"top left", transform:`scale(${scale})`});
  }

  fitViewport(message) {
    const view = this.viewport;
    if (!view) return;
    const {width, height, viewport_width:vw, viewport_height:vh} = message;
    if (![width,height,vw,vh].every(n => Number.isFinite(n) && n > 0)) return;
    if (Math.abs(vw - Math.ceil(view.width / view.scale)) > 1 || Math.abs(vh - Math.ceil(view.height / view.scale)) > 1) return;
    if (view.autoHeight && width <= vw + 1 && height * view.scale > view.height + 2 && view.passes < 8) {
      // Preserve readable width in the editor. Grow the surrounding page instead
      // of shrinking a portrait game into a short, wide strip. Bound games whose
      // own height is always larger than 100vh, leaving their scrolling available.
      const desired = Math.min(4096, Math.ceil(height * view.scale) + 2);
      if (desired > view.height + 2) {
        view.passes++;
        view.height = desired;
        const border = this.container.offsetHeight - this.container.clientHeight;
        this.container.style.setProperty("--game-height", (desired + border) + "px");
        this.applyViewport();
        return;
      }
    }
    const ratio = Math.min(1, width <= vw + 1 ? 1 : vw / width, view.autoHeight || height <= vh + 1 ? 1 : vh / height);
    // Ignore rounding and bound viewport-dependent games (e.g. 100vh + a
    // footer). Their own scrolling remains available instead of shrinking forever.
    if (ratio >= .998 || view.passes >= 8) return;
    const scale = Math.max(.1, view.scale * ratio);
    if (view.scale - scale < .001) return;
    view.scale = scale;
    view.passes++;
    this.applyViewport();
  }

  unload() {
    this.iframe = null;
    this.ready = null;
    this.viewport = null;
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
    if (message?.type === "game_viewport") { this.fitViewport(message); return; }
    if (!message || message.type !== "make_move") {
      this.onBadMessage(`ignored a message that isn't make_move: ${JSON.stringify(message)}`);
      return;
    }
    // A v2 game sends only its move; the server validates and applies it.
    if ("move" in message) {
      this.onMakeMove({ move: message.move });
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
    this.onMakeMove({ new_state: message.new_state, next_turn_player_indices: next,
      ...(message.payout_player_indices !== undefined ? {payout_player_indices: message.payout_player_indices} : {}) });
  }
}

/** The state_changed message for a viewer; see README.md for each field.
 * For a v2 game, [view] is the viewer's viewFor view of the match; the
 * client renders it instead of [state] (which then carries the same view
 * for portal matches, and the full state only in the builder's emulator,
 * where the author may see everything). */
function stateChangedMessage({ state, players, turn, status, endReason, moveCount, mySeat, actingFor, view }) {
  const message = {
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
  if (view !== undefined) message.view = view;
  return message;
}

function statusClass(status) {
  if (status >= 200 && status < 300) return "ok";
  if (status >= 400 && status < 500) return "warn";
  return "error";
}

/** Dock beside the page when 280px of navigation leaves at least 620px to work. */
function setUpSidebarDrawer() {
  const sidebar=document.querySelector('.builder-shell > .sidebar, .portal-shell > .sidebar');
  if (!sidebar) return;
  const main=document.querySelector('.work'), openers=[];
  const wide=window.matchMedia('(min-width: 900px)');
  let opened=false, returnFocus=null;
  sidebar.id ||= 'app-sidebar';
  const close=el('button',{type:'button',class:'sidebar-close','aria-label':'Close sidebar',textContent:'← Close sidebar',onclick:()=>setOpen(false)});
  sidebar.prepend(close);
  const backdrop=el('button',{type:'button',class:'sidebar-backdrop',tabIndex:-1,'aria-label':'Close sidebar',onclick:()=>setOpen(false)});
  document.body.append(backdrop);
  function render() {
    const modal=opened && !wide.matches;
    document.body.classList.toggle('sidebar-open',opened);
    document.body.classList.toggle('sidebar-docked',wide.matches);
    document.body.classList.toggle('sidebar-modal',modal);
    sidebar.inert=!opened;
    sidebar.setAttribute('aria-hidden',String(!opened));
    sidebar.setAttribute('role',wide.matches?'complementary':'dialog');
    sidebar.setAttribute('aria-label',document.querySelector('.builder-shell')?'Your games':'Your matches');
    if (modal) sidebar.setAttribute('aria-modal','true');
    else sidebar.removeAttribute('aria-modal');
    main.inert=modal;
    for (const button of openers) button.setAttribute('aria-expanded',String(opened));
  }
  function setOpen(value,opener) {
    const changed = opened !== Boolean(value);
    const rect = sidebar.animate ? sidebar.getBoundingClientRect() : null;
    sidebar.getAnimations?.().forEach(animation => animation.cancel());
    opened=Boolean(value);
    if (opened) returnFocus=opener || document.activeElement;
    render();
    if (changed && rect && document.querySelector('.portal-shell') && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const geometry = opened ? {} : {position:'fixed', top:`${rect.top}px`, left:`${rect.left}px`, width:`${rect.width}px`, height:`${rect.height}px`, zIndex:120};
      sidebar.animate?.([
        {...geometry, visibility:'visible', opacity:opened ? .3 : 1, transform:opened ? 'translateX(-24px)' : 'translateX(0)'},
        {...geometry, visibility:'visible', opacity:opened ? 1 : 0, transform:opened ? 'translateX(0)' : 'translateX(-24px)'}
      ], {duration:220, easing:'ease-out'});
    }
    if (opened) close.focus();
    else if (returnFocus) { returnFocus.focus({preventScroll:true}); returnFocus=null; }
  }
  for (const toolbar of [document.querySelector('.topbar'),document.querySelector('.match-toolbar')].filter(Boolean)) {
    const button=el('button',{type:'button',class:'sidebar-toggle',textContent:'☰','aria-label':'Open sidebar','aria-controls':sidebar.id,'aria-expanded':'false'});
    button.addEventListener('click',()=>setOpen(!opened,button));
    toolbar.prepend(button);openers.push(button);
  }
  sidebar.addEventListener('click',event=>{
    if (!wide.matches && event.target.closest('.list-item, #new-game, #show-creator')) setOpen(false);
  });
  document.addEventListener('keydown',event=>{
    if (!opened) return;
    if (event.key==='Escape') {event.preventDefault();setOpen(false);}
    if (event.key==='Tab' && !wide.matches) {
      const controls=[...sidebar.querySelectorAll('button:not([disabled]),a[href],summary,input:not([disabled]),select,textarea,[tabindex="0"]')].filter(node=>node.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if (event.shiftKey && document.activeElement===first) {event.preventDefault();last?.focus();}
      else if (!event.shiftKey && document.activeElement===last) {event.preventDefault();first?.focus();}
    }
  });
  wide.addEventListener('change',()=>{render();if(opened && !wide.matches)close.focus();});
  setOpen(false);
}
if (document.readyState==='complete'||document.readyState==='interactive') setUpSidebarDrawer();
else document.addEventListener?.('DOMContentLoaded',setUpSidebarDrawer,{once:true});
