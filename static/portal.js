// Portal: list your matches, create, join, start, leave and delete them, and play them.

// The selected match's game runs in a sandboxed iframe; the page polls the server and
// sends the game state_changed whenever the match changes.
// Identity comes from Auth (the account menu); data comes from the public and
// authenticated API endpoints, never from the debug tools.

"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 2000;

const state = {
  games: [], // latest versions, from GET /games
  gameCache: new Map(), // "game_id@version" -> game (null when gone)
  nameCache: new Map(), // user id -> display name
  myMatches: [],
  openMatches: [],
  selectedId: null,
  match: null, // the selected match, from GET /matches/{id}
  armed: null, // an action waiting for a confirming second click
  loaded: null, // "match_id@game_id@version" loaded in the iframe
  lastSent: null, // the last state_changed sent, as JSON
  moveInFlight: false,
  refreshing: false,
  refreshAgain: false,
  creating: false,
  revealedMatch: null,
  gameSearchOpen: false,
  gameSearchQuery: "",
  gameSearchActive: 0,
  gameSearchOriginal: "",
  gameSearchKey: null,
  filteredGames: [],
};

const frame = new GameFrame($("game-box"), (move) => onMakeMove(move), (problem) => showMessage(problem, "error"));

// Data

function me() {
  return Auth.current();
}

/** A display name from the cache, or a short id while it loads. */
function nameOf(userId) {
  return state.nameCache.get(userId) ?? shortId(userId);
}

function playerName(player) {
  return player.user_id ? nameOf(player.user_id) : `Computer ${player.player_index + 1}`;
}

function gameOf(gameId, version) {
  return state.gameCache.get(`${gameId}@${version}`) ?? null;
}

function seatOf(match, userId) {
  return match.players.find((player) => player.user_id === userId) ?? null;
}

/** The seat this viewer moves for now: their own turn, or a computer's turn if they play. */
function actingFor(match, userId) {
  if (match.status !== "ongoing" || match.turn_of_player_indices === null || !userId) return null;
  const seat = seatOf(match, userId);
  if (!seat) return null;
  const turn = match.turn_of_player_indices;
  if (turn.includes(seat.player_index)) return seat.player_index;
  const computer = turn.map((index) => match.players[index]).find((player) => player && player.kind === "computer");
  return computer ? computer.player_index : null;
}

function isYourTurn(match, userId) {
  const seat = seatOf(match, userId);
  return match.status === "ongoing" && seat && (match.turn_of_player_indices ?? []).includes(seat.player_index);
}

function isJoinable(match, userId) {
  if (!userId || seatOf(match, userId)) return false;
  const game = gameOf(match.game_id, match.game_version);
  if (!game) return false;
  if (game.published === false) return false;
  const max = Math.max(...game.allowed_player_counts);
  if (match.status === "waiting_for_players") return match.players.length < max;
  if (match.status === "ongoing" && game.allows_join_mid_match) {
    return match.players.some((player) => player.kind === "computer") || match.players.length < max;
  }
  return false;
}

/** Fetches the names and game versions for everything on screen. */
async function ensureDetails() {
  const userIds = new Set();
  const gameKeys = new Set();
  const collect = (match) => {
    userIds.add(match.owner_user_id);
    for (const player of match.players) if (player.user_id) userIds.add(player.user_id);
    gameKeys.add(`${match.game_id}@${match.game_version}`);
  };
  state.myMatches.forEach(collect);
  state.openMatches.forEach(collect);
  if (state.match) collect(state.match);
  await Promise.all([
    ...[...userIds]
      .filter((id) => !state.nameCache.has(id))
      .map(async (id) => {
        try {
          state.nameCache.set(id, (await apiRequest("GET", `/users/${id}`)).display_name);
        } catch {
          state.nameCache.set(id, shortId(id));
        }
      }),
    ...[...gameKeys]
      .filter((key) => !state.gameCache.has(key))
      .map(async (key) => {
        const at = key.lastIndexOf("@");
        try {
          state.gameCache.set(
            key,
            await apiRequest("GET", `/games/${key.slice(0, at)}/versions/${key.slice(at + 1)}`, {user: me()})
          );
        } catch {
          state.gameCache.set(key, null);
        }
      }),
  ]);
}

async function refresh() {
  if (state.refreshing) {
    // Refresh again once the current one finishes, so it sees the latest changes.
    state.refreshAgain = true;
    return;
  }
  state.refreshing = true;
  state.refreshAgain = false;
  try {
    const user = me();
    const [games, myMatches, openMatches] = await Promise.all([
      apiRequest("GET", "/games"),
      user ? apiRequest("GET", "/matches", { user }) : [],
      apiRequest("GET", "/matches/open"),
    ]);
    state.games = games;
    state.myMatches = myMatches;
    state.openMatches = openMatches;
    if (state.selectedId) {
      try {
        state.match = await apiRequest("GET", `/matches/${state.selectedId}`, {user});
      } catch (error) {
        if (error.status !== 404) throw error;
        showMessage("This match no longer exists.", "error");
        state.match = null;
      }
    }
    await ensureDetails();
    render();
    await syncGame();
    showPageError("");
  } catch (error) {
    showPageError(`Couldn't load data from the server: ${error.message}`);
  } finally {
    state.refreshing = false;
  }
  if (state.refreshAgain) await refresh();
}

// Lists

function matchItem(match) {
  const game = gameOf(match.game_id, match.game_version);
  const user = me();
  const yourTurn = user && isYourTurn(match, user.id);
  const badges = [el("span", { class: `badge ${match.status}`, textContent: match.status.replaceAll("_", " ") })];
  if (yourTurn) badges.unshift(el("span", { class: "badge turn", textContent: "your turn" }));
  return el(
    "button",
    {
      type: "button",
      class: "list-item",
      "aria-selected": String(match.id === state.selectedId),
      onclick: () => selectMatch(match.id),
    },
    el("div", { class: "title" }, game ? game.name : "?", ...badges),
    el("div", {
      class: "sub",
      textContent: `${match.players.map(playerName).join(", ")} · ${match.move_count} moves`,
    }),
  );
}

function renderLists() {
  const user = me();
  const groups = [
    ["turn", (m) => user && isYourTurn(m, user.id)],
    ["ongoing", (m) => m.status === "ongoing"],
    ["waiting", (m) => m.status === "waiting_for_players"],
    ["over", (m) => m.status === "over"],
  ];
  const shown = new Set();
  for (const [id, predicate] of groups) {
    const matches = state.myMatches.filter((m) => !shown.has(m.id) && predicate(m));
    matches.forEach((m) => shown.add(m.id));
    $("count-" + id).textContent = String(matches.length);
    if (id === "turn") $("group-turn").hidden = matches.length === 0;
    renderMatchList($("list-" + id), matches);
    if (matches.some((m) => m.id === state.selectedId) && state.revealedMatch !== state.selectedId) {
      $("group-" + id).open = true;
      state.revealedMatch = state.selectedId;
    }
  }
  $("portal-login-note").hidden = Boolean(user);

  const open = user ? state.openMatches.filter((m) => isJoinable(m, user.id)) : state.openMatches;
  $("count-open").textContent = String(open.length);
  renderMatchList($("open-matches"), open);

  const games = state.games;
  const select = $("new-game");
  const previous = select.value;
  const key = JSON.stringify(games.map((game) => [game.id, game.version]));
  if (select.dataset.key !== key) {
    select.dataset.key = key;
    const requested = new URLSearchParams(location.hash.slice(1)).get("game");
    const chosen = requested || previous;
    if (!state.gameSearchOpen) select.value = games.some((game) => game.id === chosen) ? chosen : games[0]?.id ?? "";
    else if (!games.some((game) => game.id === previous)) select.value = "";
  }
  renderGameSearch();
  updateComputerLimit();
}

function gameCountLabel(game) {
  return game.allowed_player_counts.length === 1 && game.allowed_player_counts[0] === 1
    ? "1 player · solo" : `${game.allowed_player_counts.join(", ")} players`;
}

function renderGameSearch() {
  const input = $("game-search");
  const selected = state.games.find((game) => game.id === $("new-game").value);
  if (!state.gameSearchOpen) input.value = selected?.name ?? state.gameSearchQuery;
  input.setAttribute("aria-expanded", String(state.gameSearchOpen));
  $("game-search-popup").hidden = !state.gameSearchOpen;
  const query = state.gameSearchQuery.trim().toLocaleLowerCase();
  const results = state.games.filter((game) => `${game.name} ${game.description || ""}`.toLocaleLowerCase().includes(query));
  state.filteredGames = results.slice(0, 12);
  state.gameSearchActive = Math.min(state.gameSearchActive, Math.max(0, state.filteredGames.length - 1));
  const key = JSON.stringify([state.gameSearchOpen, state.gameSearchQuery, state.gameSearchActive,
    results.map((game) => [game.id, game.version])]);
  if (key === state.gameSearchKey) return;
  state.gameSearchKey = key;
  input.setAttribute("aria-activedescendant", state.gameSearchOpen && state.filteredGames.length ? `game-result-${state.gameSearchActive}` : "");
  $("game-results").replaceChildren(...state.filteredGames.map((game, index) => el("button", {
    id: `game-result-${index}`, type: "button", role: "option", tabIndex: -1,
    class: "game-option", "aria-selected": String(index === state.gameSearchActive),
    onclick: () => chooseGame(game.id),
  }, el("span", {textContent: game.name}), el("small", {textContent: gameCountLabel(game)}))));
  $("game-results-note").textContent = results.length === 0 ? "No matching games. Try a different name."
    : results.length > 12 ? `Showing 12 of ${results.length} games. Keep typing to narrow the list.` : `${results.length} ${results.length === 1 ? "game" : "games"} found. Use arrow keys and Enter to select.`;
}

function chooseGame(id) {
  if (!state.games.some((game) => game.id === id)) return;
  $("new-game").value = id;
  state.gameSearchOpen = false;
  state.gameSearchQuery = "";
  state.gameSearchOriginal = id;
  $("create-message").textContent = "";
  renderGameSearch();
  updateComputerLimit();
}

function closeGameSearch(restore = false) {
  if (!state.gameSearchOpen) return;
  if (restore && !$("new-game").value && state.games.some((game) => game.id === state.gameSearchOriginal)) $("new-game").value = state.gameSearchOriginal;
  state.gameSearchOpen = false;
  if (restore || $("new-game").value) state.gameSearchQuery = "";
  renderGameSearch();
  updateComputerLimit();
}

function onGameSearchInput() {
  state.gameSearchOpen = true;
  state.gameSearchQuery = $("game-search").value;
  state.gameSearchActive = 0;
  $("new-game").value = "";
  renderGameSearch();
  updateComputerLimit();
}

function onGameSearchKey(event) {
  if (event.key === "Escape") { event.preventDefault(); closeGameSearch(true); return; }
  if (event.key === "Enter" && state.gameSearchOpen) {
    event.preventDefault();
    const game = state.filteredGames[state.gameSearchActive];
    if (game) chooseGame(game.id);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const wasOpen = state.gameSearchOpen;
    state.gameSearchOpen = true;
    if (wasOpen) state.gameSearchActive = (state.gameSearchActive + (event.key === "ArrowDown" ? 1 : -1) + Math.max(1, state.filteredGames.length)) % Math.max(1, state.filteredGames.length);
    renderGameSearch();
    $(`game-result-${state.gameSearchActive}`)?.scrollIntoView?.({block:"nearest"});
  }
}

function renderMatchList(node, matches) {
  // Keep the native details/summary nodes stable: polling must not reopen groups,
  // move keyboard focus, or replace unchanged buttons while someone is clicking.
  const key = JSON.stringify([matches, state.selectedId, [...state.nameCache],
    matches.map((m) => gameOf(m.game_id, m.game_version)?.name)]);
  if (node.dataset.key === key) return;
  node.dataset.key = key;
  node.replaceChildren(...(matches.length ? matches.map(matchItem)
    : [el("div", {class: "empty-note", textContent: "No matches here."})]));
}

// The selected match

function updateComputerLimit() {
  const game = state.games.find((game) => game.id === $("new-game").value);
  const maximum = game ? Math.max(...game.allowed_player_counts) - 1 : 0;
  $("computers").max = String(maximum);
  $("computers").value = String(Math.min(maximum, Math.max(0, Number($("computers").value) || 0)));
  $("computers").disabled = maximum === 0;
  $("create").disabled = !me() || !game || state.creating;
  const solo = game?.allowed_player_counts.length === 1 && maximum === 0;
  $("create").textContent = solo ? "Play solo ↗" : "Create match ↗";
  $("computer-setting").hidden = !game || solo;
  $("selected-game-card").hidden = !game;
  $("selected-game-name").textContent = game?.name || "Choose a game";
  $("selected-game-description").textContent = game?.description || "";
  $("selected-game-mode").textContent = solo ? "1 player · solo" : game ? `${game.allowed_player_counts.join(", ")} players` : "";
  $("create-hint").textContent = solo ? "Starts immediately. No opponent needed." : game ? "With 0 computers, invite friends using your match link. Start once everyone is seated." : state.games.length ? "Choose a game from the search results to continue." : "Publish a game in the builder to make it available here.";
}

function selectMatch(id) {
  if (id === state.selectedId) return;
  state.selectedId = id;
  state.match = null;
  state.armed = null;
  state.lastSent = null;
  $("match-creator").open = false;
  showMessage("");
  history.replaceState(null, "", id ? `#match=${id}` : "#");
  refresh();
}

function showMessage(text, kind = "") {
  $("match-message").textContent = text;
  $("match-message").className = `message ${kind}`;
}

function actionButton(label, run, { primary = false, danger = false, confirm = null, disabled = false } = {}) {
  const armed = confirm && state.armed === label;
  return el("button", {
    type: "button",
    class: primary ? "primary" : danger ? "danger" : "",
    disabled,
    textContent: armed ? `${label}: click again to confirm` : label,
    title: confirm ?? "",
    onclick: async () => {
      if (confirm && !armed) {
        state.armed = label;
        showMessage(confirm, "error");
        render();
        return;
      }
      state.armed = null;
      try {
        await run();
        showMessage("");
      } catch (error) {
        showMessage(error.message, "error");
      }
      await refresh();
    },
  });
}

function renderMatch() {
  const match = state.match;
  const user = me();
  $("match").hidden = !match;
  $("placeholder").hidden = Boolean(match);
  if (!match) {
    $("placeholder").textContent = user
      ? "Your match will open here. You can also select a match from the sidebar."
      : "Log in at the top right, then choose a game above.";
    return;
  }
  const game = gameOf(match.game_id, match.game_version);
  const counts = game ? game.allowed_player_counts : [];
  const ownerName = nameOf(match.owner_user_id);
  $("match-title").textContent = `${game ? game.name : "?"} (version ${match.game_version})`;
  $("match-status").textContent = match.status.replaceAll("_", " ");
  $("match-status").className = `badge ${match.status}`;
  const meta = [`owner ${ownerName}`, `${match.move_count} moves`, `created ${formatTime(match.created_at)}`];
  if (match.end_reason) meta.push(match.end_reason === "finished" ? "ended by the game" : "ended because a player left");
  if (game && game.deleted) meta.push("this game has been deleted");
  else if (game?.published === false) meta.push("unpublished · existing participants only");
  $("match-meta").textContent = meta.join(" · ");

  const mySeat = user ? seatOf(match, user.id) : null;
  const turn = match.turn_of_player_indices ?? [];
  $("seats").replaceChildren(...match.players.map((player) =>
    el("span", {
      class: `seat-chip${turn.includes(player.player_index) ? " turn" : ""}`,
      textContent: `${player.player_index}: ${playerName(player)}${player === mySeat ? " (you)" : ""}` +
        `${turn.includes(player.player_index) ? " — to move" : ""}`,
    })));

  const actions = [];
  const isOwner = user && match.owner_user_id === user.id;
  const latest = state.games.find((item) => item.id === match.game_id);
  const path = `/matches/${match.id}`;
  const call = (method, suffix = "", body = undefined) => () => apiRequest(method, path + suffix, { user, body });
  if (user && isJoinable(match, user.id)) {
    actions.push(actionButton("Join", call("POST", "/join"), { primary: true }));
  }
  if (match.status === "waiting_for_players") {
    if (isOwner) {
      if (latest && latest.version > match.game_version && latest.allowed_player_counts.length === 1 && latest.allowed_player_counts[0] === 1) {
        actions.push(actionButton("Play latest solo version", async () => {
          $("new-game").value = latest.id;
          renderGameSearch();
          updateComputerLimit();
          await createMatch();
        }, {primary: true}));
      }
      const computers = match.players.filter((player) => player.kind === "computer").length;
      const max = Math.max(...counts, 0);
      actions.push(
        actionButton("− Computer", call("PATCH", "", { num_computer_opponents: computers - 1 }), { disabled: computers === 0 }),
        actionButton("+ Computer", call("PATCH", "", { num_computer_opponents: computers + 1 }), { disabled: match.players.length >= max }),
        actionButton("Start", call("POST", "/start"), { primary: true, disabled: !counts.includes(match.players.length) }),
        actionButton("Delete", call("DELETE"), { danger: true, confirm: "Delete this match for everyone?" }));
    } else if (mySeat) {
      actions.push(actionButton("Leave", call("POST", "/leave"), { danger: true }));
    }
  } else if (match.status === "ongoing" && mySeat) {
    const confirm = game && game.allows_leave_mid_match
      ? "Leave? A computer takes over your seat."
      : "Leave? This game doesn't allow leaving, so the match ends for everyone.";
    actions.push(actionButton("Leave", call("POST", "/leave"), { danger: true, confirm }));
  } else if (match.status === "over") {
    if (isOwner) actions.push(actionButton("Delete", call("DELETE"), { danger: true, confirm: "Delete this match for everyone?" }));
    else if (mySeat) actions.push(actionButton("Hide from my list", call("DELETE")));
  }
  if (isOwner && match.status === "ongoing") {
    actions.push(actionButton("Delete", call("DELETE"), { danger: true, confirm: "Delete this ongoing match for everyone?" }));
  }
  actions.push(el("button", {
    type: "button",
    textContent: "Copy link",
    onclick: () => navigator.clipboard.writeText(`${location.origin}/portal#match=${match.id}`).then(() => toast("Copied the link")),
  }));
  $("actions").replaceChildren(...actions);

  const waiting = match.status === "waiting_for_players";
  $("waiting").hidden = !waiting;
  if (waiting) {
    const allowed = counts.length ? counts.join(", ") : "?";
    const ready = counts.includes(match.players.length);
    if (ready) $("match-status").textContent = "ready to start";
    $("waiting").textContent = ready
      ? `Ready to start with ${match.players.length} ${match.players.length === 1 ? "player" : "players"}. ` + (isOwner ? "Press Start to play." : "The owner starts the match.")
      : `Waiting for players: ${match.players.length} seated, this game plays with ${allowed}. ` +
        (isOwner ? "Add computers or wait for others to join, then start." : "The owner starts the match.");
    if (latest && latest.version > match.game_version) {
      $("waiting").textContent += ` This match uses version ${match.game_version}; the latest game is version ${latest.version}. Create a new match to use its updated rules.`;
    }
  }
  $("game-box").hidden = waiting;
}

function render() {
  renderLists();
  renderMatch();
}

// The game iframe

async function gameCode(match) {
  const key = `${match.game_id}@${match.game_version}`;
  let game = state.gameCache.get(key);
  if (!game) {
    game = await apiRequest("GET", `/games/${match.game_id}/versions/${match.game_version}`, {user: me()});
    state.gameCache.set(key, game);
  }
  return game.code;
}

async function syncGame() {
  const match = state.match;
  if (!match || match.status === "waiting_for_players") {
    if (state.loaded) frame.unload();
    state.loaded = null;
    state.lastSent = null;
    return;
  }
  const key = `${match.id}@${match.game_id}@${match.game_version}`;
  if (state.loaded !== key) {
    frame.load(await gameCode(match));
    state.loaded = key;
    state.lastSent = null;
  }
  const user = me();
  const mySeat = user ? seatOf(match, user.id) : null;
  const message = stateChangedMessage({
    state: match.state,
    players: match.players.map((player) => ({
      player_index: player.player_index,
      kind: player.kind,
      name: playerName(player),
    })),
    turn: match.turn_of_player_indices,
    status: match.status,
    endReason: match.end_reason,
    moveCount: match.move_count,
    mySeat: mySeat ? mySeat.player_index : null,
    actingFor: user ? actingFor(match, user.id) : null,
  });
  const json = JSON.stringify(message);
  if (json !== state.lastSent) {
    state.lastSent = json;
    frame.send(message);
  }
}

async function onMakeMove(move) {
  const match = state.match;
  const user = me();
  if (!match || state.moveInFlight) return;
  if (!user || actingFor(match, user.id) === null) {
    showMessage("Ignored a move from the game: it isn't your turn.", "error");
    return;
  }
  state.moveInFlight = true;
  try {
    state.match = await apiRequest("POST", `/matches/${match.id}/moves`, {
      user,
      body: { ...move, expected_move_count: match.move_count },
    });
    showMessage("");
  } catch (error) {
    // Another player's client may have submitted a computer's move first.
    if (!(error.status === 409 && error.message.startsWith("expected"))) {
      showMessage(`The server rejected the move: ${error.message}`, "error");
    }
  } finally {
    state.moveInFlight = false;
    state.lastSent = null;
    await refresh();
  }
}

// Creating matches

async function createMatch() {
  if (state.creating) return;
  const user = me();
  if (!user) {
    $("create-message").textContent = "Log in first.";
    $("create-message").className = "message error";
    return;
  }
  if (!state.games.some((game) => game.id === $("new-game").value)) {
    $("create-message").textContent = "Choose a game from the search results first.";
    return;
  }
  let match;
  state.creating = true;
  $("create").disabled = true;
  const game = state.games.find((item) => item.id === $("new-game").value);
  try {
    match = await apiRequest("POST", "/matches", {
      user,
      body: { game_id: $("new-game").value, num_computer_opponents: Number($("computers").value) || 0 },
    });
    // Solo-only games have no lobby or second seat to wait for. Start the pinned
    // version returned by the server; if the catalog changed, leave Start available.
    if (game && game.version === match.game_version && game.allowed_player_counts.length === 1 && game.allowed_player_counts[0] === 1 && match.players.length === 1) {
      match = await apiRequest("POST", `/matches/${match.id}/start`, {user, body: {first_turn_player_indices: [0]}});
    }
    $("create-message").textContent = "";
    selectMatch(match.id);
  } catch (error) {
    if (match) selectMatch(match.id);
    $("create-message").textContent = match ? `Match created, but couldn't start automatically: ${error.message}. Press Start to retry.` : error.message;
    $("create-message").className = "message error";
  } finally {
    state.creating = false;
    renderLists();
  }
}

function selectFromHash() {
  const id = new URLSearchParams(location.hash.slice(1)).get("match");
  if (id && id !== state.selectedId) selectMatch(id);
}

function main() {
  Auth.renderMenu($("account-menu"));
  document.addEventListener("auth-changed", () => {
    Auth.renderMenu($("account-menu"));
    state.armed = null;
    state.lastSent = null;
    state.gameCache.clear();
    state.nameCache.clear();
    frame.unload();
    state.loaded = null;
    showMessage("");
    refresh();
  });
  $("game-search").addEventListener("focus", () => {
    if (state.gameSearchOpen) return;
    state.gameSearchOriginal = $("new-game").value;
    state.gameSearchOpen = true;
    state.gameSearchQuery = "";
    state.gameSearchActive = 0;
    $("game-search").select();
    renderGameSearch();
  });
  $("game-search").addEventListener("input", onGameSearchInput);
  $("game-search").addEventListener("keydown", onGameSearchKey);
  $("game-chooser").addEventListener("focusout", (event) => {
    if (!$("game-chooser").contains(event.relatedTarget)) closeGameSearch();
  });
  document.addEventListener("click", (event) => {
    if (!$("game-chooser").contains(event.target)) closeGameSearch();
  });
  $("show-creator").addEventListener("click", () => {
    $("match-creator").open = true;
    $("match-creator").scrollIntoView({behavior: "smooth", block: "start"});
    $("game-search").focus({preventScroll: true});
  });
  $("create").addEventListener("click", createMatch);
  window.addEventListener("hashchange", selectFromHash);
  selectFromHash();
  refresh();
  setInterval(refresh, POLL_MS);
}

main();
