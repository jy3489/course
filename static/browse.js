// Browse page: shows every user, game (with all versions) and match (with all moves)
// from /debug/all-data.

"use strict";

const AUTO_REFRESH_KEY = "game-platform.browse.auto-refresh";
const AUTO_REFRESH_MS = 2000;

const state = {
  allData: null,
  // Keys of rows whose details are open, kept across refreshes.
  expanded: new Set(),
  loadedAt: null,
};

const $ = (id) => document.getElementById(id);

function json(value) {
  return JSON.stringify(value, null, 2);
}

function tag(text, kind = text) {
  return el("span", { class: `tag ${kind}`, textContent: text });
}

function yesNo(value) {
  return value ? "yes" : "no";
}

/** A table whose rows can be filtered and, if they have [detail], expanded. */
function renderTable(container, countNode, columns, rows) {
  const filter = $("filter").value.trim().toLowerCase();
  const visible = rows.filter((row) => !filter || row.text.toLowerCase().includes(filter));
  countNode.textContent =
    visible.length === rows.length ? `${rows.length}` : `${visible.length} of ${rows.length}`;
  if (!visible.length) {
    const message = rows.length ? `No rows match "${filter}".` : "None yet.";
    container.replaceChildren(el("div", { class: "empty", textContent: message }));
    return;
  }
  const deletable = visible.some((row) => row.remove);
  const allColumns = deletable ? [...columns, { title: "" }] : columns;
  const body = el("tbody");
  for (const row of visible) {
    const expandable = Boolean(row.detail);
    const expanded = expandable && state.expanded.has(row.key);
    const tr = el(
      "tr",
      {
        class: [expandable ? "row" : "", expanded ? "expanded" : ""].join(" "),
        onclick: expandable ? () => toggle(row.key) : undefined,
      },
      [
        ...row.cells.map((cell, index) =>
          el("td", { class: columns[index].class ?? "" }, cell ?? ""),
        ),
        deletable
          ? el(
              "td",
              {},
              row.remove
                ? el("button", {
                    class: "danger",
                    textContent: "Delete",
                    onclick: (event) => {
                      event.stopPropagation();
                      deleteRows([row]);
                    },
                  })
                : "",
            )
          : null,
      ],
    );
    body.append(tr);
    if (expanded) {
      body.append(el("tr", { class: "detail" }, el("td", { colSpan: allColumns.length }, row.detail())));
    }
  }
  container.replaceChildren(
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, allColumns.map((column) => el("th", { class: column.class ?? "", textContent: column.title })))),
      body,
    ),
    deletable
      ? el("button", {
          class: "danger",
          textContent: `Delete these ${visible.filter((row) => row.remove).length} rows`,
          onclick: () => deleteRows(visible.filter((row) => row.remove)),
        })
      : null,
  );
}

/** Deletes rows through the admin API after a confirmation; the list shows the count. */
async function deleteRows(rows) {
  const names = rows.slice(0, 5).map((row) => row.remove.label).join(", ");
  const more = rows.length > 5 ? ` and ${rows.length - 5} more` : "";
  if (!confirm(`Delete ${rows.length} row(s): ${names}${more}?
This can't be undone.`)) return;
  const failures = [];
  for (const row of rows) {
    const response = await fetch(row.remove.path, { method: "DELETE" });
    if (!response.ok) failures.push(`${row.remove.label}: ${response.status}`);
  }
  $("error").textContent = failures.length ? `Couldn't delete: ${failures.join("; ")}` : "";
  toast(`Deleted ${rows.length - failures.length} row(s)`);
  state.allData = null;
  await refresh();
}

function toggle(key) {
  if (state.expanded.has(key)) state.expanded.delete(key);
  else state.expanded.add(key);
  render();
}

function render() {
  const data = state.allData;
  if (!data) return;
  const names = userNames(data);
  const nameOf = (userId) => names.get(userId) ?? shortId(userId);
  const games = latestGames(data);
  const gamesById = new Map(games.map((game) => [game.id, game]));
  const versionsById = new Map();
  for (const game of data.game_versions) {
    if (!versionsById.has(game.id)) versionsById.set(game.id, []);
    versionsById.get(game.id).push(game);
  }

  renderUsers(data, games);
  renderGames(data, games, versionsById, nameOf);
  renderMatches(data, gamesById, nameOf);
  renderConversations(data, gamesById);
}

function renderConversations(data, gamesById) {
  const columns = [
    { title: "Game" },
    { title: "Game ID" },
    { title: "Messages", class: "num" },
    { title: "Last message", class: "time" },
  ];
  const conversations = data.agent_conversations ?? [];
  const rows = conversations.map((conversation) => {
    const game = gamesById.get(conversation.game_id);
    const name = game ? game.name : conversation.game_id;
    const last = conversation.entries[conversation.entries.length - 1];
    return {
      key: `conversation:${conversation.game_id}`,
      text: `${name} ${conversation.game_id}`,
      cells: [
        name,
        idChip(conversation.game_id),
        conversation.entries.length,
        last ? formatTime(last.created_at) : "",
      ],
      detail: () =>
        el(
          "div",
          {},
          conversation.entries.map((entry) =>
            el(
              "div",
              { class: "chat-entry" },
              el("div", {
                class: "chat-meta",
                textContent: `${entry.role === "user" ? "User" : "Agent"} · ${formatTime(entry.created_at)} · ${
                  entry.game_version ? `game version ${entry.game_version}` : "not yet published"
                }`,
              }),
              el("div", { class: "chat-text", textContent: entry.text }),
            ),
          ),
        ),
    };
  });
  renderTable($("conversations"), $("conversations-count"), columns, rows);
}

function renderUsers(data, games) {
  const columns = [
    { title: "Name" },
    { title: "ID" },
    { title: "Password" },
    { title: "Games owned", class: "num" },
    { title: "Matches", class: "num" },
    { title: "Created", class: "time" },
  ];
  const rows = data.users.map((user) => {
    const ownedGames = games.filter((game) => game.owner_user_id === user.id && !game.deleted);
    const matches = data.matches.filter(
      (match) =>
        match.owner_user_id === user.id ||
        match.players.some((player) => player.user_id === user.id),
    );
    return {
      key: `user:${user.id}`,
      text: `${user.display_name} ${user.id}`,
      remove: { path: `/admin/users/${encodeURIComponent(user.id)}`, label: user.display_name },
      cells: [
        user.display_name,
        idChip(user.id),
        copyable(user.password),
        ownedGames.length,
        matches.length,
        formatTime(user.created_at),
      ],
    };
  });
  renderTable($("users"), $("users-count"), columns, rows);
}

function renderGames(data, games, versionsById, nameOf) {
  const columns = [
    { title: "Name" },
    { title: "ID" },
    { title: "Version", class: "num" },
    { title: "Owner" },
    { title: "Player counts" },
    { title: "Leave mid-match" },
    { title: "Join mid-match" },
    { title: "Status" },
    { title: "Matches", class: "num" },
    { title: "Created", class: "time" },
    { title: "Updated", class: "time" },
  ];
  const rows = games.map((game) => {
    const matches = data.matches.filter((match) => match.game_id === game.id);
    return {
      key: `game:${game.id}`,
      remove: game.deleted ? null : { path: `/admin/games/${encodeURIComponent(game.id)}`, label: game.name },
      text: `${game.name} ${game.id} ${nameOf(game.owner_user_id)} ${game.deleted ? "deleted" : "live"}`,
      cells: [
        game.name,
        idChip(game.id),
        game.version,
        nameOf(game.owner_user_id),
        game.allowed_player_counts.join(", "),
        yesNo(game.allows_leave_mid_match),
        yesNo(game.allows_join_mid_match),
        game.deleted ? tag("deleted") : tag("live"),
        matches.length,
        formatTime(game.created_at),
        formatTime(game.updated_at),
      ],
      detail: () => gameDetail(versionsById.get(game.id), matches),
    };
  });
  renderTable($("games"), $("games-count"), columns, rows);
}

function gameDetail(versions, matches) {
  const versionRows = [...versions].reverse().map((version) => {
    const usedBy = matches.filter((match) => match.game_version === version.version).length;
    return el(
      "tr",
      {},
      el("td", { class: "num", textContent: version.version }),
      el("td", { textContent: version.name }),
      el("td", { class: "wrap", textContent: version.description }),
      el("td", { textContent: version.allowed_player_counts.join(", ") }),
      el("td", { textContent: yesNo(version.allows_leave_mid_match) }),
      el("td", { textContent: yesNo(version.allows_join_mid_match) }),
      el("td", { class: "num", textContent: usedBy }),
      el("td", { class: "time", textContent: formatTime(version.updated_at) }),
      el(
        "td",
        { class: "wrap" },
        el(
          "details",
          {},
          el("summary", { textContent: `${version.code.length} chars` }),
          el("pre", { class: "json", textContent: version.code }),
        ),
      ),
    );
  });
  return el(
    "div",
    {},
    el("div", { class: "detail-title", textContent: "Versions (newest first)" }),
    el(
      "table",
      {},
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          ["Version", "Name", "Description", "Player counts", "Leave", "Join", "Matches", "Saved", "Code"].map(
            (title) => el("th", { textContent: title }),
          ),
        ),
      ),
      el("tbody", {}, versionRows),
    ),
  );
}

function seats(match, nameOf) {
  const turn = match.turn_of_player_indices ?? [];
  return el(
    "span",
    {},
    match.players.map((player, index) => [
      index ? ", " : "",
      el("span", {
        class: `seat${turn.includes(player.player_index) ? " turn" : ""}`,
        title: turn.includes(player.player_index) ? "Has the turn" : "",
        textContent: `${player.player_index}:${player.user_id ? nameOf(player.user_id) : "computer"}`,
      }),
    ]),
  );
}

function renderMatches(data, gamesById, nameOf) {
  const columns = [
    { title: "ID" },
    { title: "Game" },
    { title: "Owner" },
    { title: "Status" },
    { title: "End reason" },
    { title: "Players (turn underlined)" },
    { title: "Moves", class: "num" },
    { title: "Hidden for" },
    { title: "Created", class: "time" },
    { title: "Updated", class: "time" },
  ];
  const rows = data.matches.map((match) => {
    const game = gamesById.get(match.game_id);
    const gameName = game ? game.name : "?";
    const players = match.players.map((player) => (player.user_id ? nameOf(player.user_id) : "computer"));
    return {
      key: `match:${match.id}`,
      remove: { path: `/admin/matches/${encodeURIComponent(match.id)}`, label: match.id },
      text: [match.id, gameName, nameOf(match.owner_user_id), match.status, match.end_reason ?? "", ...players].join(" "),
      cells: [
        idChip(match.id),
        `${gameName} v${match.game_version}`,
        nameOf(match.owner_user_id),
        tag(match.status),
        match.end_reason ?? "",
        seats(match, nameOf),
        match.move_count,
        match.hidden_for_user_ids.map(nameOf).join(", "),
        formatTime(match.created_at),
        formatTime(match.updated_at),
      ],
      detail: () => matchDetail(match, nameOf),
    };
  });
  renderTable($("matches"), $("matches-count"), columns, rows);
}

function matchDetail(match, nameOf) {
  const consoleLink = `/console#${new URLSearchParams({ op: "GET /matches/{match_id}", match_id: match.id })}`;
  const moveRows = match.moves.map((move) => {
    const seat = match.players.find((player) => player.player_index === move.player_index);
    const seatName = seat && seat.user_id ? nameOf(seat.user_id) : "computer";
    return el(
      "tr",
      {},
      el("td", { class: "num", textContent: move.move_number }),
      el("td", { textContent: `${move.player_index} (${seatName} now)` }),
      el("td", { textContent: nameOf(move.made_by_user_id) }),
      el("td", { class: "time", textContent: formatTime(move.created_at) }),
      el("td", { class: "num", textContent: move.next_turn_player_indices === null ? "end" : move.next_turn_player_indices.join(", ") }),
      el("td", { class: "wrap" }, el("pre", { textContent: JSON.stringify(move.new_state) })),
    );
  });
  return el(
    "div",
    { class: "detail-grid" },
    el(
      "div",
      {},
      el("div", { class: "detail-title", textContent: "Current state" }),
      el("pre", { class: "json", textContent: json(match.state) }),
      el("p", {}, el("a", { href: consoleLink, textContent: "Open in API console" })),
    ),
    el(
      "div",
      {},
      el("div", { class: "detail-title", textContent: "Moves" }),
      moveRows.length
        ? el(
            "table",
            {},
            el(
              "thead",
              {},
              el(
                "tr",
                {},
                ["#", "Seat", "Submitted by", "At", "Next turn", "New state"].map((title) =>
                  el("th", { textContent: title }),
                ),
              ),
            ),
            el("tbody", {}, moveRows),
          )
        : el("div", { class: "empty", textContent: "No moves yet." }),
    ),
  );
}

async function refresh() {
  try {
    if (await StoragePages.enabled()) {
      await refreshPagedBrowse();
      state.loadedAt=new Date();
      $("error").textContent="";
      renderFreshness();
      return;
    }
    const allData = await fetchAllData();
    state.loadedAt = new Date();
    $("error").textContent = "";
    // Re-rendering resets open code sections and text selections, so only do it when
    // something changed.
    if (JSON.stringify(allData) !== JSON.stringify(state.allData)) {
      state.allData = allData;
      render();
    }
  } catch (error) {
    $("error").textContent = `Couldn't load data: ${error.message}`;
  }
  renderFreshness();
}

function renderFreshness() {
  $("freshness").textContent = state.loadedAt ? `Loaded ${formatTime(state.loadedAt.toISOString())}` : "Loading…";
}

let autoRefreshTimer = null;
function setAutoRefresh(enabled) {
  localStorage.setItem(AUTO_REFRESH_KEY, enabled ? "1" : "");
  clearInterval(autoRefreshTimer);
  autoRefreshTimer = enabled ? setInterval(refresh, AUTO_REFRESH_MS) : null;
}

function main() {
  let filterTimer;
  $("filter").addEventListener("input",()=>{clearTimeout(filterTimer);filterTimer=setTimeout(()=>state.pages?refresh():render(),250);});
  $("refresh").addEventListener("click", refresh);
  const autoRefresh = $("auto-refresh");
  autoRefresh.checked = Boolean(localStorage.getItem(AUTO_REFRESH_KEY));
  autoRefresh.addEventListener("change", () => setAutoRefresh(autoRefresh.checked));
  setAutoRefresh(autoRefresh.checked);
  renderFreshness();
  refresh();
}

main();

async function refreshPagedBrowse() {
  const mappings=[["users","users"],["games","games"],["matches","matches"],["agent_conversations","conversations"]];
  state.pages ??= {};
  for (const [kind,id] of mappings) {
    const page=state.pages[kind] ??= {cursor:null,previous:[]};
    const response=await fetch(`/v2/admin/${kind}?limit=50${page.cursor?"&cursor="+encodeURIComponent(page.cursor):""}`);
    if(!response.ok)throw new Error(`Loading ${kind}: ${response.status}`);
    const result=await response.json();
    const filter=$("filter").value.toLocaleLowerCase();
    const rows=result.items.filter(x=>JSON.stringify(x).toLocaleLowerCase().includes(filter));
    const back=el("button",{textContent:"Previous page",disabled:!page.previous.length,onclick:()=>{page.cursor=page.previous.pop();refresh();}});
    const next=el("button",{textContent:"Next page",disabled:!result.next_cursor,onclick:()=>{page.previous.push(page.cursor);page.cursor=result.next_cursor;refresh();}});
    $(id+"-count").textContent=`${rows.length} on this page`;
    $(id).replaceChildren(...rows.map(row=>{
      const detail=el("details",{},el("summary",{textContent:row.display_name||row.name||row.id||row.game_id}),el("pre",{textContent:JSON.stringify(row,null,2)}));
      if(kind==="agent_conversations") detail.append(el("button",{textContent:"Load recent messages",onclick:async()=>{
        const r=await fetch(`/admin/games/${encodeURIComponent(row.game_id)}/agent-conversation`);
        if(r.ok)detail.append(el("pre",{textContent:JSON.stringify(await r.json(),null,2)}));
      }}));
      return detail;
    }),back,next);
  }
}
