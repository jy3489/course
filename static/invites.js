// Invites page: who brought whom to the platform, from /admin/invites.
//
// Every user knows who invited them (the "ref" of the invite link they signed up
// through). "Total" counts everyone a user brought in transitively: the people they
// invited, the people those people invited, and so on.

"use strict";

const $ = (id) => document.getElementById(id);

/** Replaces the contents of element [id], skipping empty (null) parts. */
function show(id, ...parts) {
  $(id).replaceChildren(...parts.filter((part) => part != null));
}

const LEADERBOARD_SIZE = 20;
const MAX_SEARCH_RESULTS = 15;

const state = { users: [], byId: new Map(), children: new Map(), selectedId: null };

function card(value, label) {
  return el("div", { class: "card" }, el("div", { class: "value", textContent: value }), el("div", { class: "label", textContent: label }));
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** A user's name as a button that opens their details. */
function userLink(user) {
  return el("button", {
    type: "button",
    class: "linkish",
    textContent: user.display_name,
    title: user.id,
    onclick: () => select(user.id),
  });
}

/** A table of users; clicking a row opens that user's details. */
function userTable(users, { rank = false } = {}) {
  const headers = [...(rank ? ["#"] : []), "User", "Total", "Direct", "Invited by"];
  return el(
    "table",
    {},
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { class: ["#", "Total", "Direct"].includes(h) ? "num" : "", textContent: h })))),
    el(
      "tbody",
      {},
      users.map((user, i) =>
        el(
          "tr",
          { class: `pick${user.id === state.selectedId ? " selected" : ""}`, onclick: () => select(user.id) },
          rank ? el("td", { class: "num", textContent: i + 1 }) : null,
          el("td", { textContent: user.display_name, title: user.id }),
          el("td", { class: "num", textContent: user.total_invites }),
          el("td", { class: "num", textContent: user.direct_invites }),
          el("td", { class: "muted", textContent: user.invited_by_name || "—" }),
        ),
      ),
    ),
  );
}

function renderSummary(summary) {
  $("summary").replaceChildren(
    el(
      "div",
      { class: "cards" },
      card(summary.users, "users"),
      card(summary.invited_users, "came through an invite link"),
      card(summary.inviters, "invited someone"),
      card(summary.biggest_tree, "people in the biggest invite tree"),
      card(summary.longest_chain, "invites in the longest chain"),
    ),
  );
}

function renderLeaderboard() {
  const top = state.users.filter((user) => user.total_invites > 0).slice(0, LEADERBOARD_SIZE);
  $("leaderboard").replaceChildren(
    top.length ? userTable(top, { rank: true }) : el("div", { class: "muted", textContent: "Nobody has invited anyone yet." }),
  );
}

function renderSearch() {
  const query = $("search").value.trim().toLowerCase();
  if (!query) {
    $("results").replaceChildren();
    return;
  }
  const matches = state.users.filter(
    (user) => user.display_name.toLowerCase().includes(query) || user.id.toLowerCase().includes(query),
  );
  show(
    "results",
    matches.length
      ? userTable(matches.slice(0, MAX_SEARCH_RESULTS))
      : el("div", { class: "muted", textContent: "No user matches." }),
    matches.length > MAX_SEARCH_RESULTS
      ? el("div", { class: "muted", textContent: `Showing ${MAX_SEARCH_RESULTS} of ${matches.length}; type more to narrow it down.` })
      : null,
  );
}

/** The users [userId] invited, and below them everyone they brought in. */
function subtree(userId, { open = false } = {}) {
  const kids = state.children.get(userId) ?? [];
  return el(
    "ul",
    { class: "tree" },
    kids.map((child) => {
      const label = [userLink(child), el("span", { class: "count", textContent: child.total_invites ? `brought ${child.total_invites}` : "" })];
      return el(
        "li",
        {},
        child.direct_invites
          ? el("details", { open }, el("summary", {}, label), subtree(child.id))
          : el("div", { class: "leaf" }, label),
      );
    }),
  );
}

/** From the person who came without an invite down to [user]. */
function chainTo(user) {
  const chain = [];
  const seen = new Set();
  for (let current = user; current && !seen.has(current.id); current = state.byId.get(current.invited_by_user_id)) {
    seen.add(current.id);
    chain.unshift(current);
  }
  return chain;
}

function renderDetail() {
  const user = state.byId.get(state.selectedId);
  if (!user) {
    $("detail").replaceChildren();
    return;
  }
  const chain = chainTo(user);
  show(
    "detail",
    el("h3", { textContent: user.display_name, style: "margin-top: var(--space-md)" }),
    el("div", { class: "muted", textContent: `${user.id} · joined ${formatTime(user.created_at)}` }),
    el(
      "div",
      { class: "chain", "aria-label": "Invite chain" },
      chain.length > 1
        ? chain.flatMap((link, i) => [i ? el("span", { class: "muted", textContent: "→" }) : null, link.id === user.id ? el("strong", { textContent: link.display_name }) : userLink(link)])
        : el("span", { class: "muted", textContent: "Came without an invite link." }),
    ),
    el("p", {
      textContent: user.total_invites
        ? `Brought ${plural(user.total_invites, "user")} in total: ${user.direct_invites} directly and ${user.total_invites - user.direct_invites} through the people they invited.`
        : "Hasn't invited anyone yet.",
    }),
    user.direct_invites ? subtree(user.id, { open: true }) : null,
  );
}

function renderTree() {
  const roots = state.users.filter((user) => !user.invited_by_user_id);
  const withInvites = roots.filter((user) => user.direct_invites);
  const alone = roots.length - withInvites.length;
  show(
    "tree",
    withInvites.length
      ? el(
          "ul",
          { class: "tree" },
          withInvites.map((root) =>
            el(
              "li",
              {},
              el("details", {}, el("summary", {}, userLink(root), el("span", { class: "count", textContent: `brought ${root.total_invites}` })), subtree(root.id)),
            ),
          ),
        )
      : el("div", { class: "muted", textContent: "No invites yet." }),
    alone ? el("p", { class: "muted", textContent: `${plural(alone, "other user")} came without an invite link and haven't invited anyone.` }) : null,
  );
}

function select(userId) {
  state.selectedId = userId;
  renderLeaderboard();
  renderSearch();
  renderDetail();
  $("detail").scrollIntoView?.({ block: "nearest" });
}

function load(stats) {
  state.users = stats.users;
  state.byId = new Map(stats.users.map((user) => [user.id, user]));
  state.children = new Map();
  // The server sorts users by total invites, so each child list comes out sorted too.
  for (const user of stats.users) {
    if (!user.invited_by_user_id) continue;
    if (!state.children.has(user.invited_by_user_id)) state.children.set(user.invited_by_user_id, []);
    state.children.get(user.invited_by_user_id).push(user);
  }
  renderSummary(stats.summary);
  renderLeaderboard();
  renderSearch();
  renderDetail();
  renderTree();
}

async function refresh() {
  try {
    const response = await fetch("/admin/invites");
    if (!response.ok) throw new Error(`GET /admin/invites: ${response.status}`);
    const stats = await response.json();
    $("error").textContent = "";
    load(stats);
    $("freshness").textContent = `Loaded ${formatTime(stats.generated_at)}`;
  } catch (error) {
    $("error").textContent = `Couldn't load the invites: ${error.message}`;
  }
}

$("refresh").addEventListener("click", refresh);
$("search").addEventListener("input", renderSearch);
refresh();
