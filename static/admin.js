// Admin hub: tabs that embed the super-admin tools (API console, data browser,
// statistics, invites, API docs) as iframes. The /browse, /stats and /invites pages
// need the master password; the browser reuses the credentials entered for /admin
// itself.

"use strict";

const $ = (id) => document.getElementById(id);

const TABS = ["console", "browse", "stats", "invites", "docs"];
const loaded = new Set();

function showTab(name) {
  for (const tab of TABS) {
    $(`tab-${tab}`).setAttribute("aria-selected", String(name === tab));
  }
  const src = $(`tab-${name}`).dataset.src;
  const wrap = $("admin-frame-wrap");
  if (!loaded.has(name)) {
    const frame = document.createElement("iframe");
    frame.title = $(`tab-${name}`).textContent;
    frame.src = src;
    // Keep every tab's page alive so switching tabs doesn't lose state.
    frame.hidden = true;
    frame.dataset.tab = name;
    wrap.append(frame);
    loaded.add(name);
  }
  for (const frame of wrap.querySelectorAll("iframe")) {
    frame.hidden = frame.dataset.tab !== name;
  }
  const note = wrap.querySelector(".empty-note");
  if (note) note.remove();
  location.hash = name;
}

function main() {
  for (const tab of TABS) {
    $(`tab-${tab}`).addEventListener("click", () => showTab(tab));
  }
  const initial = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "console";
  Auth.renderMenu($("account-menu"));
  document.addEventListener("auth-changed", () => Auth.renderMenu($("account-menu")));
  showTab(initial);
}

main();
