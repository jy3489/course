// Portal: list your matches, create, join, start, leave and delete them, and play them.

// The selected match's game runs in a sandboxed iframe; the page polls the server and
// sends the game state_changed whenever the match changes.
// Identity comes from Auth (the account menu); data comes from the public and
// authenticated API endpoints, never from the debug tools.

"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 2000;
async function portalTransport(method, path, { body, user } = {}) {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 6000);
  try {
    const headers = {};
    const campaignCode = window.ExperimentCampaignCode?.();
    if (campaignCode) headers["X-Campaign-Code"] = campaignCode;
    if (user) {
      headers["X-User-Id"] = user.id;
      headers["X-User-Password"] = user.password;
    }
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await response.text();
    let data;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      throw new Error("Invalid server response");
    }
    if (!response.ok)
      throw new ApiError(response.status, data?.detail || response.statusText);
    return data;
  } finally {
    clearTimeout(timer);
  }
}
const offline = new PortalOffline(localStorage, portalTransport, () =>
  Auth.current(),
);
const portalRequest = (method, path, options = {}) =>
  offline.api(method, path, { user: Auth.current(), ...options });
const portalLock = (run) =>
  navigator.locks
    ? navigator.locks.request("playroom-portal", run)
    : Promise.reject(
        new Error(
          "Offline moves need a current browser with Web Locks support.",
        ),
      );
let shellReady = false;
const RANDOM_GAME = {
  id: "__random__",
  name: "Random",
  description: "Join an open match of any game",
  allowed_player_counts: [2],
};
const isRandomGame = (game) => game === RANDOM_GAME;
function offlineStatus() {
  window.PortalMatchLayout?.connection(navigator.onLine && offline.network);
  const data = offline.read();
  const pending = Object.values(data.queues).reduce((n, q) => n + q.length, 0);
  const conflicts = Object.keys(data.conflicts).length;
  $("offline-status").textContent =
    `${navigator.onLine && offline.network ? "Online" : "Offline"} · ${pending} pending moves${conflicts ? ` · ${conflicts} sync conflicts` : ""} · ${shellReady ? "Portal saved for offline" : "Offline setup not ready"}`;
}

// Web push notifications: the toggle subscribes this browser and registers it with
// the server, which then pushes turn and match events. The logic is shared with the
// builder (push-notifications.js); arriving pushes are shown by portal-sw.js.
function setupNotifications() {
  return PushNotifications.setup($("notify-toggle"), portalTransport);
}

function toggleNotifications() {
  return PushNotifications.toggle($("notify-toggle"), portalTransport);
}

function currentPushSubscription() {
  return PushNotifications.currentSubscription();
}

const state = {
  games: [], // latest versions, from GET /games
  gameCache: new Map(), // "game_id@version" -> game (null when gone)
  pictureCache: new Map(),
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
  randomJoining: false,
  revealedMatch: null,
  gameSearchOpen: false,
  gameSearchQuery: "",
  gameSearchActive: 0,
  gameSearchOriginal: "",
  gameSearchKey: null,
  filteredGames: [],
  libraryFilter: "all",
  libraryView: "grid",
  libraryKey: null,
  requestedGameHandled: false,
  detailCreatorInitialized: false,
  detailGameId:
    new URLSearchParams(location.search).get("game") ||
    new URLSearchParams(location.hash.slice(1)).get("game"),
};

class PortalGameFrame extends GameFrame {
  receive(event) {
    if (event.source !== this.iframe?.contentWindow) return;
    const data = event.data;
    if (
      data?.type === "make_move" &&
      data.next_turn_player_indices === undefined &&
      data.next_turn_player_index === null
    )
      return super.receive({
        source: event.source,
        data: { ...data, next_turn_player_indices: null },
      });
    super.receive(event);
  }
}
const frame = new PortalGameFrame(
  $("game-box"),
  (move) => onMakeMove(move),
  (problem) => showMessage(problem, "error"),
);

// Data

// TOKEN_UI_POLISH_V1: backend-owned balances; never award or settle tokens in the browser.
let walletBalance = null;
function updateStakePreview() {
  const select = $("match-stake"), preview = $("stake-preview"), panel = $("token-stake-panel");
  if (!select || !preview || !panel) return;
  const game = state.games.find(g => g.id === (state.detailGameId || $("new-game").value));
  const solo = Boolean(game && game.allowed_player_counts.length === 1 && game.allowed_player_counts[0] === 1);
  panel.classList.toggle("is-solo", solo);
  select.disabled = solo;
  if (solo && select.value !== "0") select.value = "0";
  const amount = Number(select.value);
  if (!amount || solo) {
    preview.replaceChildren(el("span", {class:"stake-preview-text", textContent: solo ? "Solo game · No wager" : "Free game · No tokens at risk"}));
  } else {
    const extra = walletBalance === null ? "" : ` · Wallet: ${walletBalance} tokens`;
    preview.replaceChildren(
      el("span", {class:"stake-preview-text", textContent:`${amount} tokens per person · ${amount * 2} tokens in a 2-player pot${extra}`}));
  }
}

async function updateTokenWallet(user) {
  const wallet = $("token-wallet");
  if (!user) { wallet.hidden = true; walletBalance = null; updateStakePreview(); return; }
  try {
    const record = await portalTransport("GET", `/users/${encodeURIComponent(user.id)}`, {user});
    if (me()?.id !== user.id) return;
    // Accounts saved before short invite links learn their referral code here.
    state.pictureCache.set(user.id, record.picture_url);
    state.nameCache.set(user.id, record.display_name);
    if (record.referral_code) Auth.setReferralCode?.(user.id, record.referral_code);
    if (Auth.setPicture?.(user.id, record.picture_url)) Auth.renderMenu($("account-menu"));
    if (!Number.isSafeInteger(record.token_balance) || record.token_balance < 0) {
      wallet.hidden = true; walletBalance = null; updateStakePreview(); return;
    }
    walletBalance = record.token_balance;
    const balance = String(walletBalance);
    if ($("token-balance").textContent !== balance) $("token-balance").textContent = balance;
    wallet.title = `${balance} virtual tokens · Earn 10 when starting your first eligible match each day`;
    wallet.hidden = false;
    // No optimistic balance adjustments: the server may grant today's +10 at start.
    updateStakePreview();
  } catch {
    wallet.hidden = true; walletBalance = null; updateStakePreview();
  }
}

function updateMatchCurrency(match, user) {
  const box = $("match-currency");
  if (!match || match.localMode) { box.hidden = true; return; }
  const stake = match.bet_per_player ?? 0;
  const pot = match.token_pot ?? 0;
  if (!Number.isSafeInteger(stake) || !Number.isSafeInteger(pot)) { box.hidden = true; return; }
  const paid = stake > 0;
  box.className = `match-currency token-summary${paid ? " is-paid" : ""}`;
  const title = el("div", {class:"token-summary-title"},
    el("span", {class:"token-summary-icon", "aria-hidden":"true", textContent:"✦"}),
    el("strong", {textContent: paid ? "Match stakes" : "Free match"}));
  const values = el("div", {class:"token-summary-values"},
    el("div", {}, el("span", {textContent:"Per player"}), el("strong", {textContent:`${stake} tokens`})),
    el("div", {}, el("span", {textContent:"Pot"}), el("strong", {textContent:`${pot} tokens`})));
  const notes = [];
  if (paid && match.status === "waiting_for_players")
    notes.push(el("p", {class:"token-summary-note", textContent:"Pot is collected when the match starts. Paid matches need human players."}));
  if (paid && match.status === "over" && match.token_payouts != null) {
    const seat = user && match.players.find(p => p.user_id === user.id);
    if (seat) {
      const payout = match.token_payouts[String(seat.player_index)] ?? 0;
      const net = payout - stake;
      notes.push(el("div", {class:"token-summary-payout"},
        el("span", {textContent:"Your payout"}),
        el("strong", {textContent:`${payout} tokens`}),
        el("small", {textContent:`Net ${net > 0 ? "+" : ""}${net} tokens`})));
    } else notes.push(el("p", {class:"token-summary-note", textContent:"Payouts settled for all players."}));
  }
  if (!paid && match.status === "over")
    notes.push(el("p", {class:"token-summary-note", textContent:"No tokens were wagered in this game."}));
  box.replaceChildren(title, values, ...notes);
  box.hidden = false;
}


function me() {
  return Auth.current();
}

/** A display name from the cache, or a short id while it loads. */
function nameOf(userId) {
  return state.nameCache.get(userId) ?? shortId(userId);
}

function playerName(player) {
  if (player.kind === "human" && !player.user_id)
    return `Player ${player.player_index + 1}`;
  return player.user_id
    ? nameOf(player.user_id)
    : I18n.t("ui.match.computer_name", { number: player.player_index + 1 }, `Computer ${player.player_index + 1}`);
}

function gameOf(gameId, version) {
  return state.gameCache.get(`${gameId}@${version}`) ?? null;
}

function seatOf(match, userId) {
  return match.players.find((player) => player.user_id === userId) ?? null;
}

/** The seat this viewer moves for now: their own turn, or — in a v1 game — a
 * computer's turn, which the viewer's browser plays. In a v2 game computers
 * move on the server, so a viewer only ever acts for their own seat. */
function actingFor(match, userId) {
  if (
    match.status !== "ongoing" ||
    match.turn_of_player_indices === null ||
    !userId
  )
    return null;
  if (match.localMode === "pass") return match.turn_of_player_indices[0];
  const seat = seatOf(match, userId);
  if (!seat) return null;
  const turn = match.turn_of_player_indices;
  if (turn.includes(seat.player_index)) return seat.player_index;
  const game = gameOf(match.game_id, match.game_version);
  if (game && game.server_code) return null;
  const computer = turn
    .map((index) => match.players[index])
    .find((player) => player && player.kind === "computer");
  return computer ? computer.player_index : null;
}

function isYourTurn(match, userId) {
  const seat = seatOf(match, userId);
  return match.status === "ongoing" && seat && (match.turn_of_player_indices ?? []).includes(seat.player_index);
}

function isJoinable(match, userId) {
  if (match.localMode || !userId || seatOf(match, userId)) return false;
  const game = gameOf(match.game_id, match.game_version);
  if (!game) return false;
  if (game.published === false) return false;
  const max = Math.max(...game.allowed_player_counts);
  if (match.status === "waiting_for_players") return match.players.length < max;
  if (match.status === "ongoing" && game.allows_join_mid_match) {
    return (
      match.players.some((player) => player.kind === "computer") ||
      match.players.length < max
    );
  }
  return false;
}

/** Fetches the names and game versions for everything on screen. */
async function ensureDetails() {
  const userIds = new Set();
  const gameKeys = new Set();
  const collect = (match) => {
    userIds.add(match.owner_user_id);
    for (const player of match.players)
      if (player.user_id) userIds.add(player.user_id);
    gameKeys.add(`${match.game_id}@${match.game_version}`);
  };
  state.myMatches.forEach(collect);
  state.openMatches.forEach(collect);
  if (state.match) collect(state.match);
  await Promise.all([
    ...[...userIds]
      .filter((id) => !state.nameCache.has(id) || !state.pictureCache.get(id))
      .map(async (id) => {
        try {
          const profile = await portalRequest("GET", `/users/${id}`);
          state.nameCache.set(id, profile.display_name);
          state.pictureCache.set(id, profile.picture_url);
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
            await portalRequest(
              "GET",
              `/games/${key.slice(0, at)}/versions/${key.slice(at + 1)}`,
            ),
          );
        } catch {
          // A transient network error must not permanently poison this version.
        }
      }),
  ]);
}

async function refresh() {
  return portalLock(refreshLocked).catch((error) =>
    showPageError(error.message),
  );
}

async function refreshLocked() {
  if (state.refreshing) {
    // Refresh again once the current one finishes, so it sees the latest changes.
    state.refreshAgain = true;
    return;
  }
  state.refreshing = true;
  state.refreshAgain = false;
  try {
    const user = me();
    if (!user) $("token-wallet").hidden = true;
    await offline.sync();
    const [games, myMatches, openMatches] = await Promise.all([
      portalRequest("GET", "/games"),
      user ? portalRequest("GET", "/matches", { user }) : [],
      portalRequest("GET", "/matches/open"),
    ]);
    state.games = games;
    state.myMatches = myMatches;
    state.openMatches = openMatches;
    if (state.selectedId) {
      try {
        state.match = await portalRequest(
          "GET",
          `/matches/${state.selectedId}`,
        );
      } catch (error) {
        if (error.status !== 404) throw error;
        showMessage("This match no longer exists.", "error");
        state.replay = null;
        state.match = null;
      }
    }
    await ensureDetails();
    if (user?.id !== me()?.id) return;
    await updateTokenWallet(user);
    if (user?.id !== me()?.id) return;
    render();
    window.PortalFeedback?.update(state.match, user?.id, {
      suppressed: Boolean(state.replay || state.match?.pending || state.match?.conflict || state.match?.localMode || !navigator.onLine),
      yourTurn: Boolean(user && state.match && isYourTurn(state.match, user.id)),
      outcome: state.match ? matchOutcome(state.match, user?.id)?.kind : null,
    });
    await syncGame();
    showPageError("");
    offlineStatus();
  } catch (error) {
    window.PortalFeedback?.reset();
    showPageError(`Couldn't load data from the server: ${error.message}`);
  } finally {
    state.refreshing = false;
    offlineStatus();
  }
  if (state.refreshAgain) await refreshLocked();
}

// Lists

// Games own their opaque state. Only display an outcome when they explicitly
// report a valid winning seat or a completed draw; never infer a loss from leaving.
function matchOutcome(match, userId) {
  if (match.status !== "over") return null;
  if (match.end_reason && match.end_reason !== "finished")
    return {label:"Ended early", kind:"neutral"};
  const result = match.state;
  const seat = seatOf(match, userId);
  let outcome = {label:"Result unavailable", kind:"neutral"};
  if (result && typeof result === "object") {
    if (Number.isInteger(result.winner) && match.players.some(player => player.player_index === result.winner)) {
      outcome = seat
        ? {label:seat.player_index === result.winner ? "You won" : "You lost", kind:seat.player_index === result.winner ? "win" : "loss"}
        : {label:`${playerName(match.players.find(player => player.player_index === result.winner))} won`, kind:"neutral"};
    } else if (result.winner === -1 || (result.winner === null && (result.draw === true || result.done === true))) {
      outcome = {label:"Draw", kind:"draw"};
    }
  }
  if (match.pending || match.conflict) outcome = {...outcome, label:outcome.label + " · awaiting sync"};
  return outcome;
}

function matchItem(match) {
  const game = gameOf(match.game_id, match.game_version);
  const user = me();
  const yourTurn = user && isYourTurn(match, user.id);
  const badges = [el("span", { class: `badge ${match.status}`, textContent: I18n.t(`status.${match.status}`, {}, match.status.replaceAll("_", " ")) })];
  const outcome = matchOutcome(match, user?.id);
  if (outcome) badges.push(el("span", {class:`badge result-${outcome.kind}`, textContent:outcome.label}));
  if (yourTurn) badges.unshift(el("span", { class: "badge turn", textContent: I18n.t("ui.match.your_turn", {}, "your turn") }));
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
      textContent: `${match.players.map(playerName).join(", ")} · ${I18n.t("ui.match.moves_count", { count: match.move_count }, `${match.move_count} moves`)}`,
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
    if (state.detailGameId) {
      select.value = games.some((game) => game.id === state.detailGameId) ? state.detailGameId : "";
    } else {
      const requested = state.requestedGameHandled ? null : new URLSearchParams(location.hash.slice(1)).get("game");
      if (games.length) state.requestedGameHandled = true;
      const chosen = requested || previous;
      if (!state.gameSearchOpen) select.value = games.some((game) => game.id === chosen) ? chosen : "";
      else if (!games.some((game) => game.id === previous)) select.value = "";
    }
  }
  renderGameSearch();
  updateMatchSetup();
}

function openGamePage(id) {
  if (!state.games.some((game) => game.id === id)) return;
  location.assign(`/static/portal.html?game=${encodeURIComponent(id)}`);
}

async function joinRandomMatch() {
  if (state.randomJoining) return;
  const user = me();
  if (!user) {
    showPageError("Log in to join a random match.");
    return;
  }
  if (!navigator.onLine) {
    showPageError("Connect to the internet to join a random match.");
    return;
  }
  state.randomJoining = true;
  state.libraryKey = null;
  renderLibrary();
  try {
    const [openMatches, games] = await Promise.all([
      portalRequest("GET", "/matches/open"),
      portalRequest("GET", "/games"),
    ]);
    state.openMatches = openMatches;
    state.games = games;
    await ensureDetails();
    const candidates = state.openMatches.filter((match) => isJoinable(match, user.id));
    for (let i = candidates.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
    }
    for (const match of candidates) {
      try {
        await portalRequest("POST", `/matches/${match.id}/join`, {user});
        selectMatch(match.id);
        return;
      } catch (error) {
        if (error.status !== 409) throw error;
      }
    }

    const multiplayerGames = state.games.filter((game) =>
      game.published !== false && game.allowed_player_counts.some((count) => count > 1));
    if (!multiplayerGames.length)
      throw new Error("No published multiplayer games are available to create a lobby.");
    const game = multiplayerGames[Math.floor(Math.random() * multiplayerGames.length)];
    const match = await portalRequest("POST", "/matches", {
      user,
      body: {game_id: game.id, num_computer_opponents: 0},
    });
    selectMatch(match.id);
  } catch (error) {
    showPageError(`Couldn't join a random match: ${error.message}`);
  } finally {
    state.randomJoining = false;
    state.libraryKey = null;
    renderLibrary();
  }
}

function gameCountLabel(game) {
  const counts = game.allowed_player_counts.join(", ");
  return game.allowed_player_counts.length === 1 && game.allowed_player_counts[0] === 1
    ? I18n.t("ui.games.solo_label", {}, "1 player · solo")
    : I18n.t("ui.games.players_label", { counts }, `${counts} players`);
}

function libraryGames() {
  const query = state.gameSearchQuery.trim().toLocaleLowerCase();
  const matches = game =>
    `${game.name} ${game.description || ""}`.toLocaleLowerCase().includes(query) &&
    (state.libraryFilter === "all" || (state.libraryFilter === "solo"
      ? game.allowed_player_counts.includes(1)
      : game.allowed_player_counts.some(count => count > 1)));
  return [
    ...(matches(RANDOM_GAME) ? [RANDOM_GAME] : []),
    ...state.games.filter(matches),
  ];
}

// Distinguish games that share a name, even when filtering hides one of them.
function catalogGameLabel(game) {
  const name = game.name.trim().toLocaleLowerCase();
  const duplicate = state.games.some(other => other.id !== game.id && other.name.trim().toLocaleLowerCase() === name);
  const short = game.id.slice(0, 8);
  const id = state.games.some(other => other.id !== game.id && other.id.slice(0, 8) === short) ? game.id : short;
  return duplicate ? `${game.name} · v${game.version} · ${id}` : game.name;
}

function renderLibrary() {
  const games = libraryGames();
  const selected = $("new-game").value;
  const key = JSON.stringify([games, games.map(catalogGameLabel), selected, state.libraryView, state.libraryFilter, state.randomJoining, I18n.revision()]);
  if (state.libraryKey === key) return;
  state.libraryKey = key;
  $("library-count").textContent = I18n.t("ui.games.count", { count: games.length }, `${games.length} ${games.length === 1 ? "game" : "games"} to explore`);
  $("library-empty").hidden = games.length > 0;
  $("game-library").className = `game-library-grid ${state.libraryView === "list" ? "is-list" : ""}`;
  $("game-library").replaceChildren(...games.map(game => {
    const hue = Array.from(game.id).reduce((value, letter) => value + letter.charCodeAt(0), 0) % 360;
    const random = isRandomGame(game);
    // The "Random" card is built into the page; every other game's text is its builder's.
    const name = random ? I18n.t("ui.games.random_name", {}, game.name) : catalogGameLabel(game);
    const description = random ? I18n.t("ui.games.random_description", {}, game.description) : game.description;
    const initials = name.trim().split(/\s+/).slice(0, 2).map(word => Array.from(word)[0] || "").join("");
    return el("button", {type:"button", class:"library-card", "aria-pressed":String(game.id === selected),
      "aria-label":random ? `${name}. ${description}` : I18n.t("ui.games.open_aria", { name, players: gameCountLabel(game) }, `Open ${name}, ${gameCountLabel(game)}`),
      disabled:random && state.randomJoining,
      onclick:() => random ? joinRandomMatch() : openGamePage(game.id)},
      el("span", {class:"library-art", style:`--art-hue:${hue}`, "aria-hidden":"true"},
        el("span", {class:"library-monogram", textContent:initials}),
        el("span", {class:"library-art-label", textContent:"GAME PLATFORM"})),
      el("span", {class:"library-copy"}, el("span", {class:"library-mode", textContent:gameCountLabel(game)}),
        el("span", {class:"library-title", textContent:name}),
        el("span", {class:"library-description", textContent:description || I18n.t("ui.games.default_description", {}, "Choose this game to start a new match.")}),
        el("span", {class:"library-select", textContent:random ? (state.randomJoining ? I18n.t("ui.games.finding_match", {}, "Finding a match…") : I18n.t("ui.games.random_join", {}, "Join a random match ↗")) : I18n.t("ui.games.open_game", {}, "Open game ↗")})));
  }));
}

function renderGameSearch() {
  renderLibrary();
  const input = $("game-search");
  const selected = state.games.find((game) => game.id === $("new-game").value);
  if (!state.gameSearchOpen) input.value = selected?.name ?? state.gameSearchQuery;
  input.setAttribute("aria-expanded", String(state.gameSearchOpen));
  $("game-search-popup").hidden = !state.gameSearchOpen;
  const query = state.gameSearchQuery.trim().toLocaleLowerCase();
  const results = [RANDOM_GAME, ...state.games].filter((game) =>
    `${game.name} ${game.description || ""}`.toLocaleLowerCase().includes(query));
  state.filteredGames = results;
  state.gameSearchActive = Math.min(state.gameSearchActive, Math.max(0, state.filteredGames.length - 1));
  const key = JSON.stringify([state.gameSearchOpen, state.gameSearchQuery, state.gameSearchActive,
    results.map((game) => [game.id, game.version, catalogGameLabel(game)])]);
  if (key === state.gameSearchKey) return;
  state.gameSearchKey = key;
  input.setAttribute("aria-activedescendant", state.gameSearchOpen && state.filteredGames.length ? `game-result-${state.gameSearchActive}` : "");
  $("game-results").replaceChildren(...state.filteredGames.map((game, index) => el("button", {
    id: `game-result-${index}`, type: "button", role: "option", tabIndex: -1,
    class: "game-option", "aria-selected": String(index === state.gameSearchActive),
    onclick: () => isRandomGame(game) ? joinRandomMatch() : openGamePage(game.id),
  }, el("span", {class: "game-option-copy"},
    el("span", {textContent: isRandomGame(game) ? game.name : catalogGameLabel(game)}),
    ...(game.description ? [el("span", {class: "game-option-description", textContent: game.description})] : [])),
    el("small", {textContent: gameCountLabel(game)}))));
  $("game-results-note").textContent = results.length === 0 ? "No matching games. Try a different name."
    : `${results.length} ${results.length === 1 ? "game" : "games"}${query ? " found" : " available"}. Scroll to browse ${query ? "all matches" : "the full catalog"}, or type to filter. Use arrow keys and Enter to open or join a game.`;
}

function revealPortalSection(id) {
  const node = $(id);
  if (!node?.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  node.getAnimations?.().forEach(animation => animation.cancel());
  node.animate?.([{opacity:.45, transform:'translateY(5px)'}, {opacity:1, transform:'translateY(0)'}],
    {duration:180, easing:'ease-out'});
}

function browseAllGames() {
  if (!state.gameSearchOpen) state.gameSearchOriginal = $("new-game").value;
  $("game-search").value = "";
  onGameSearchInput();
  $("game-search").focus();
  revealPortalSection("game-search-popup");
}

function closeGameSearch(restore = false) {
  if (!state.gameSearchOpen) return;
  if (restore && !$("new-game").value && state.games.some((game) => game.id === state.gameSearchOriginal)) $("new-game").value = state.gameSearchOriginal;
  state.gameSearchOpen = false;
  if (restore || $("new-game").value) state.gameSearchQuery = "";
  renderGameSearch();
  updateMatchSetup();
}

function onGameSearchInput() {
  state.gameSearchOpen = true;
  state.gameSearchQuery = $("game-search").value;
  state.gameSearchActive = 0;
  $("new-game").value = "";
  renderGameSearch();
  updateMatchSetup();
}

function onGameSearchKey(event) {
  if (event.key === "Escape") { event.preventDefault(); closeGameSearch(true); return; }
  if (event.key === "Enter" && state.gameSearchOpen) {
    event.preventDefault();
    const game = state.filteredGames[state.gameSearchActive];
    if (game) isRandomGame(game) ? joinRandomMatch() : openGamePage(game.id);
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

function updateMatchSetup() {
  const detailPage = Boolean(state.detailGameId);
  if (detailPage && !state.detailCreatorInitialized) {
    $("match-creator").open = false;
    state.detailCreatorInitialized = true;
  } else if (!detailPage) {
    state.detailCreatorInitialized = false;
  }
  const game = state.games.find((game) => game.id === (detailPage ? state.detailGameId : $("new-game").value));
  const browsingGames = detailPage && $("match-creator").open;
  document.body?.classList.toggle("game-detail-mode", detailPage);
  $("game-catalog").hidden = detailPage && !browsingGames;
  $("game-detail-page").hidden = !detailPage || browsingGames || Boolean(state.selectedId);
  $("show-creator").hidden = detailPage;
  const maximum = game ? Math.max(...game.allowed_player_counts) - 1 : 0;
  $("create").disabled = !me() || !game || state.creating || !navigator.onLine;
  const serverOnly = game?.supports_local_practice === false || Boolean(game?.server_code);
  $("practice").disabled = !me() || !game || serverOnly;
  $("practice-mode").disabled = serverOnly;
  $("practice-unavailable").hidden = !serverOnly;
  $("practice-offline-note").hidden = serverOnly;
  const solo = game?.allowed_player_counts.length === 1 && maximum === 0;
  $("speed-choice").hidden = solo;
  updateStakePreview();
  $("create").textContent = solo ? I18n.t("ui.portal.play_solo", {}, "Play solo ↗") : I18n.t("ui.portal.create_match", {}, "Create match ↗");
  $("selected-game-card").hidden = !game;
  $("selected-game-name").textContent = game?.name || I18n.t("ui.games.choose", {}, "Choose a game");
  $("selected-game-description").textContent = game?.description || "";
  $("selected-game-mode").textContent = solo ? "1 player · solo" : game ? `${game.allowed_player_counts.join(", ")} players` : "";
  $("create-hint").textContent = solo ? "Starts immediately. No opponent needed." : game ? "Create a match, then invite friends or add computers in the lobby." : state.games.length ? "Choose a game from the search results to continue." : "Publish a game in the builder to make it available here.";
}

function selectMatch(id) {
  if (id === state.selectedId) return;
  state.selectedId = id;
  frame.unload();state.loaded = null;
  if (id) $("game-box").textContent = "Loading game…";
  const saved = offline.read();
  saved.selectedId = id;
  offline.write(saved);
  state.replay = null;
  state.match = null;
  state.armed = null;
  state.lastSent = null;
  if (!state.detailGameId) $("match-creator").open = false;
  showMessage("");
  const route = new URLSearchParams();
  if (state.detailGameId) route.set("game", state.detailGameId);
  if (id) route.set("match", id);
  const routeHash = route.toString();
  history.replaceState(null, "", routeHash ? `#${routeHash}` : "#");
  refresh();
}

function showMessage(text, kind = "") {
  $("match-message").textContent = text;
  $("match-message").className = `message ${kind}`;
}

function actionButton(
  label,
  run,
  { primary = false, danger = false, confirm = null, disabled = false } = {},
) {
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

function playerPicture(player) {
  if (player.kind !== "computer") {
    const account = me();
    return (account?.id === player.user_id ? account.picture_url : null) ||
      state.pictureCache.get(player.user_id) || "/static/avatars/users/puppy/puppy-natural-classic-open.png";
  }
  const animals = ["puppy", "bird", "cat", "bunny", "fox", "bear", "panda", "frog", "penguin", "otter"];
  return `/static/avatars/computers/${animals[Array.from(player.id || `computer-${player.player_index + 1}`).reduce((sum, char) => sum + char.codePointAt(0), 0) % animals.length]}.png`;
}

function renderMatch() {
  const match = state.match;
  const user = me();
  $("match").hidden = !match;
  $("placeholder").hidden = Boolean(match);
  if (!match) {
    window.PortalMatchLayout?.update(null);
    $("placeholder").textContent = user
      ? "Your match will open here. You can also select a match from the sidebar."
      : "Sign in at the top right, then choose a game above.";
    return;
  }
  const game = gameOf(match.game_id, match.game_version);
  updateMatchCurrency(match, user);
  const counts = game ? game.allowed_player_counts : [];
  const ownerName = nameOf(match.owner_user_id);
  $("match-title").textContent =
    `${game ? game.name : "?"} (version ${match.game_version})`;
  const outcome = matchOutcome(match, user?.id);
  $("match-result").hidden = !outcome;
  $("match-result").textContent = outcome?.label || "";
  $("match-result").className = `match-result result-${outcome?.kind || "neutral"}`;
  $("match-status").textContent = match.status.replaceAll("_", " ");
  $("match-status").className = `badge ${match.status}`;
  const meta = [
    `owner ${ownerName}`,
    `${match.move_count} moves`,
    `created ${formatTime(match.created_at)}`,
  ];
  if (match.turn_seconds)
    meta.push(I18n.t("ui.speed.meta", {minutes: match.turn_seconds / 60}, `speed · ${match.turn_seconds / 60} min per move`));
  if (match.end_reason)
    meta.push(
      match.end_reason === "finished"
        ? "ended by the game"
        : match.end_reason === "timed_out"
          ? I18n.t("ui.speed.ended_meta", {}, "ended because time ran out")
          : "ended because a player left",
    );
  if (game && game.deleted) meta.push("this game has been deleted");
  else if (game?.published === false) meta.push("unpublished · existing participants only");
  $("match-meta").textContent = meta.join(" · ");

  const mySeat = user ? seatOf(match, user.id) : null;
  const turn = match.turn_of_player_indices ?? [];
  $("seats").replaceChildren(
    ...match.players.map((player) =>
      el("span", {
        class: `seat-chip${turn.includes(player.player_index) ? " turn" : ""}`,
      }, el("img", {class: "player-avatar", src: playerPicture(player), alt: "", width: 44, height: 44,
        onerror: event => { event.target.hidden = true; }}), el("span", {textContent:
          `${playerName(player)}${player === mySeat ? " · You" : ""}` +
          `${turn.includes(player.player_index) ? " — to move" : ""}`,
      })),
    ),
  );

  const actions = [];
  const secondaryActions = [];
  if (match.localMode) {
    if (match.status === "ongoing") actions.push(actionButton("End practice", async()=>{offline.endPractice(match.id);state.lastSent=null;}));
    else if (match.resumeTurn) actions.push(actionButton("Resume practice",async()=>{offline.resumePractice(match.id);state.lastSent=null;}));
    secondaryActions.push(actionButton("Remove practice from list",async()=>{await portalRequest("DELETE",`/matches/${match.id}`);selectMatch(null);},{danger:true,confirm:"Archive this practice and remove it from your list?"}));
  }
  // Practice plays locally in the browser, which a v2 game can't do: its
  // logic runs on the server. The button is only offered for v1 games.
  if (game?.code && !game.server_code)
    secondaryActions.push(
      actionButton("Start a new practice", async () => {
        const practiceMatch = offline.practice(game, $("practice-mode").value);
        selectMatch(practiceMatch.id);
      }),
    );
  if ($("match-message").dataset.offline === "true") {
    showMessage("");
    delete $("match-message").dataset.offline;
  }
  if (match.pending) {
    showMessage(
      `${match.pending} move(s) saved on this device, awaiting sync.`,
    );
    $("match-message").dataset.offline = "true";
  }
  if (match.conflict) {
    showMessage(match.conflict.message, "error");
    $("match-message").dataset.offline = "true";
    actions.push(
      actionButton(
        "Use server version",
        async () => {
          offline.resolve(match.id);
          state.lastSent = null;
        },
        {
          confirm: "Archive your offline moves and restore the server version?",
        },
      ),
    );
    actions.push(
      actionButton("Download offline moves", async () => {
        const blob = new Blob(
          [JSON.stringify(offline.read().queues[match.id], null, 2)],
          { type: "application/json" },
        );
        const url = URL.createObjectURL(blob);
        const a = el("a", { href: url, download: "offline-moves.json" });
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }),
    );
  }
  const isOwner = !match.localMode && user && match.owner_user_id === user.id;
  const latest = state.games.find((item) => item.id === match.game_id);
  const path = `/matches/${match.id}`;
  const call =
    (method, suffix = "", body = undefined) =>
    async () => {
      const result = await portalRequest(method, path + suffix, {user, body});
      if (method === "DELETE") selectMatch(null);
      return result;
    };
  if (user && isJoinable(match, user.id)) {
    actions.push(
      actionButton("Join", call("POST", "/join"), { primary: true }),
    );
  }
  if (match.status === "waiting_for_players") {
    if (isOwner) {
      if (latest && latest.version > match.game_version && latest.allowed_player_counts.length === 1 && latest.allowed_player_counts[0] === 1) {
        actions.push(actionButton("Play latest solo version", async () => {
          $("new-game").value = latest.id;
          renderGameSearch();
          updateMatchSetup();
          await createMatch();
        }, {primary: true}));
      }
      const computers = match.players.filter((player) => player.kind === "computer").length;
      const paidMatch = (match.bet_per_player ?? 0) > 0;
      const max = Math.max(...counts, 0);
      if (!paidMatch) {
        const minus = actionButton("−", call("PATCH", "", {num_computer_opponents: computers - 1}), {disabled:computers === 0});
        const plus = actionButton("+", call("PATCH", "", {num_computer_opponents: computers + 1}), {disabled:match.players.length >= max});
        minus.setAttribute?.("aria-label", "Remove computer opponent");
        plus.setAttribute?.("aria-label", "Add computer opponent");
        actions.push(el("div", {class:"computer-stepper", role:"group", "aria-label":"Computer opponents"},
          el("span", {class:"stepper-label", textContent:"Computers"}), minus,
          el("span", {class:"stepper-count", textContent:String(computers), "aria-label":`${computers} computer opponents`}), plus));
      } else {
        actions.push(el("p", {class:"paid-lobby-hint", textContent: computers ?
          "Paid matches need human opponents. Remove the computer to continue." :
          "Invite a human player to join this token match."}));
        if (computers > 0) actions.push(actionButton("Remove computer", call("PATCH", "", {num_computer_opponents: computers - 1})));
      }
      const readyToStart = counts.includes(match.players.length) && (!paidMatch || computers === 0);
      actions.push(actionButton("Start game", call("POST", "/start"), {primary:true, disabled:!readyToStart}));
      secondaryActions.push(actionButton("Delete match", call("DELETE"), {danger:true, confirm:"Delete this match for everyone?"}));
    } else if (mySeat) {
      actions.push(
        actionButton("Leave", call("POST", "/leave"), { danger: true }),
      );
    }
  } else if (match.status === "ongoing" && mySeat && !match.localMode) {
    if (game && game.server_code) {
      // Leaving a v2 match: the server first previews what leaving
      // would pay back (the game's playerLeft, or the platform's
      // split), and only a confirmed click applies that exact preview
      // — so the amount is always shown, staked match or not.
      actions.push(
        actionButton("Leave", async () => {
          const preview = await portalRequest("POST", `${path}/maybeLeave`, { user });
          const payout = preview.pending_leave_payout ?? 0;
          const ends = game.allows_leave_mid_match
            ? ""
            : " Leaving ends the match for everyone.";
          if (!confirm(`You will get ${payout} tokens back — leave?${ends}`)) return;
          await portalRequest("POST", `${path}/playerLeft`, { user });
        }, { danger: true }),
      );
    } else {
      const confirm =
        game && game.allows_leave_mid_match
          ? "Leave? A computer takes over your seat."
          : "Leave? This game doesn't allow leaving, so the match ends for everyone.";
      actions.push(
        actionButton("Leave", call("POST", "/leave"), { danger: true, confirm }),
      );
    }
  } else if (match.status === "over") {
    if (mySeat && !match.localMode) {
      const playingSoloWithComputers =
        match.players.filter((player) => player.kind === "human").length === 1 &&
        match.players.some((player) => player.kind === "computer");
      actions.push(actionButton(
        playingSoloWithComputers
          ? "Play again"
          : match.rematch_match_id
            ? "Join rematch"
            : "Request rematch",
        async () => {
          const rematch = await portalRequest(
            "POST",
            `/matches/${match.id}/rematch`,
            {user},
          );
          selectMatch(rematch.id);
        },
        {primary:true},
      ));
    }
    if (isOwner)
      secondaryActions.push(
        actionButton("Delete match", call("DELETE"), {
          danger: true,
          confirm: "Delete this match for everyone?",
        }),
      );
    else if (mySeat && !match.localMode)
      actions.push(actionButton("Hide from my list", call("DELETE")));
  }
  if (isOwner && match.status === "ongoing") {
    secondaryActions.push(
      actionButton("Delete match", call("DELETE"), {
        danger: true,
        confirm: "Delete this ongoing match for everyone?",
      }),
    );
  }
  if (!match.localMode)
    actions.push(
      el("button", {
        type: "button",
        textContent: "Invite friends",
        onclick: () =>
          navigator.clipboard
            // With the sender's id as "ref", whoever signs up through the link
            // counts as invited by them.
            .writeText(
              Auth.current() && Auth.inviteLink
                ? Auth.inviteLink(Auth.current(), `match=${match.id}`)
                : `${location.origin}/portal#match=${match.id}`,
            )
            .then(() => toast("✓ Invite link copied")),
      }),
    );
  if (secondaryActions.length) {
    const menu = el("details", {class:"match-more", open:state.moreActionsMatch === match.id},
      el("summary", {"aria-label":"More match actions", textContent:"⋯"}),
      el("div", {class:"match-more-panel"}, ...secondaryActions));
    menu.addEventListener?.("toggle", () => {
      if (menu.isConnected) {
        const newlyOpened = menu.open && state.moreActionsMatch !== match.id;
        state.moreActionsMatch = menu.open ? match.id : null;
        if (menu.open) {
          const panel = menu.querySelector(".match-more-panel"), rect = menu.getBoundingClientRect();
          const width = panel.getBoundingClientRect().width, height = panel.getBoundingClientRect().height;
          panel.style.left = Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+"px";
          panel.style.top = Math.max(8,Math.min(rect.bottom+8,window.innerHeight-height-8))+"px";
          if (newlyOpened && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            panel.animate?.([{opacity:.3, translate:'0 -5px'}, {opacity:1, translate:'0 0'}],
              {duration:180, easing:'ease-out'});
          }
        }
      }
    });
    actions.push(menu);
  }
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
  window.PortalSpeed?.observe(match);
  updateSpeedClock();
  const speedNotice = window.PortalSpeed?.notice(match, user?.id, nameOf);
  window.PortalMatchLayout?.update(speedNotice ? {...match, speedNotice} : match);
}

// The 10-second alert for the player to move (sound, vibration, tab title, notification).
const speedAlerter = window.PortalSpeed?.createAlerter({
  window, document, navigator,
  Notification: typeof Notification === "undefined" ? undefined : Notification,
  AudioContext: window.AudioContext ?? window.webkitAudioContext,
  setInterval: (f, ms) => setInterval(f, ms), clearInterval: (t) => clearInterval(t),
});

/** The speed-match countdown in the toolbar (hidden in regular matches and replays).
 * When the viewer's own time is up, asks the server right away, so the takeover (or
 * the end) shows without waiting for the next poll. */
function updateSpeedClock() {
  const node = $("match-clock");
  const match = state.match;
  const speed = window.PortalSpeed;
  const shown = speed && match && state.replay?.id !== match.id ? speed.clock(match, me()?.id, nameOf) : null;
  node.hidden = !shown;
  if (!shown) {
    speedAlerter?.stop();
    return;
  }
  if (node.textContent !== shown.text) node.textContent = shown.text;
  node.classList.toggle("urgent", shown.urgent);
  speedAlerter?.update(match, me()?.id, gameOf(match.game_id, match.game_version)?.name ?? "");
  const key = `${match.id}@${match.turn_deadline}`;
  if (speed.secondsLeft(match) === 0 && state.speedRefreshFor !== key) {
    state.speedRefreshFor = key;
    refresh();
  }
}

function render() {
  renderLists();
  renderMatch();
  renderHistory();
}

// The game iframe

async function gameCode(match) {
  const key = `${match.game_id}@${match.game_version}`;
  let game = state.gameCache.get(key);
  if (!game?.code) {
    game = await portalRequest(
      "GET",
      `/games/${match.game_id}/versions/${match.game_version}`,
    );
    state.gameCache.set(key, game);
  }
  return game.code;
}

async function browseHistory(direction) {
  const match = state.match;
  if (!match || !match.move_count) return;
  if (direction === "live") state.replay = null;
  else try {
    const data = await portalRequest("GET", `/matches/${match.id}/history`, {user: me()});
    const moves = data.moves;
    const minimum = data.initial_turn_player_indices === null ? 1 : 0;
    if (state.match?.id !== match.id) return;
    const current = state.replay?.id === match.id ? state.replay.index : moves.length;
    const index = Math.max(minimum, Math.min(moves.length, current + direction));
    const move = index === 0 ? {new_state: data.initial_state, next_turn_player_indices: data.initial_turn_player_indices} : moves[index - 1];
    if (!move) return;
    state.replay = {id: match.id, index, minimum, move};
  } catch (error) { showMessage(`History unavailable: ${error.message}`, "error"); return; }
  renderHistory();await syncGame();
}
function renderHistory() {
  const match = state.match, replay = state.replay?.id === match?.id ? state.replay : null;
  const count = match?.move_count || 0;
  $("match-history").hidden = count < 1;
  $("history-position").textContent = replay ? `Viewing move ${replay.index} of ${count} · read only` : `Live · ${count} moves`;
  $("history-back").disabled = (replay?.index ?? count) <= (replay?.minimum ?? 0);
  $("history-forward").disabled = !replay || replay.index >= count;
  $("history-live").disabled = !replay;
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
    const userId = me()?.id;
    const code = await gameCode(match);
    if (state.match?.id !== match.id || me()?.id !== userId) return;
    frame.load(code);
    state.loaded = key;
    state.lastSent = null;
  }
  const user = me();
  const mySeat =
    match.localMode === "pass"
      ? { player_index: match.turn_of_player_indices?.[0] ?? 0 }
      : user
        ? seatOf(match, user.id)
        : null;
  const replay = state.replay?.id === match.id ? state.replay : null;
  const view = replay ? {...match, state: replay.move.new_state, turn_of_player_indices: replay.move.next_turn_player_indices, move_count: replay.index, status: replay.move.next_turn_player_indices === null ? "over" : "ongoing", end_reason: replay.move.next_turn_player_indices === null ? "finished" : null} : match;
  const currentGame = gameOf(match.game_id, match.game_version);
  const message = stateChangedMessage({
    state: view.state,
    // A v2 client renders its viewFor view, which is what [state] holds
    // for a participant; spectators get no view.
    view:
      currentGame && currentGame.server_code
        ? (view.state ?? null)
        : undefined,
    players: match.players.map((player) => ({
      player_index: player.player_index,
      player_id: player.id,
      kind: player.kind,
      name: playerName(player),
      picture_url: playerPicture(player),
    })),
    turn: view.turn_of_player_indices,
    status: view.status,
    // The Game API only knows "finished" and "player_left"; a speed match that ran
    // out of time is, for the game, ended by a player leaving.
    endReason: view.end_reason === "timed_out" ? "player_left" : view.end_reason,
    moveCount: view.move_count,
    mySeat: mySeat ? mySeat.player_index : null,
    actingFor: !replay && user && !match.conflict ? actingFor(match, user.id) : null,
  });
  message.token_pot = view.token_pot ?? 0;
  // Let game UIs distinguish a historical snapshot from a live opponent turn.
  message.replay = replay ? { index: replay.index, total: match.move_count } : null;
  const json = JSON.stringify(message);
  if (json !== state.lastSent) {
    state.lastSent = json;
    frame.send(message);
  }
}

async function onMakeMove(move) {
  if (state.moveInFlight || state.replay?.id === state.match?.id) return;
  const receivedFor = state.match;
  if (!receivedFor || state.loaded !== `${receivedFor.id}@${receivedFor.game_id}@${receivedFor.game_version}`) return;
  state.moveInFlight = true;
  try {
    await portalLock(async () => {
      const match = state.match,
        user = me();
      if (match?.id !== receivedFor.id) return;
      if ("move" in move) {
        // A v2 game sent only its move: the server validates and applies
        // it, so the client checks nothing but whose turn it is.
        if (!match || !user || actingFor(match, user.id) === null)
          throw new Error("It isn't your turn.");
        if (offline.read().queues?.[match.id]?.length) {
          // A v2 move is already waiting to sync. Unlike a v1 move it
          // carries no resulting state, so a second click is a decision
          // on the same stale view — queueing it behind the first could
          // only conflict once the server answers. Drop it and push the
          // pending move instead; the game replays from the synced state.
          await offline.sync({ force: true });
          return;
        }
        state.match = offline.enqueue(match, {
          move: move.move,
          expected_move_count: match.move_count,
        });
        state.lastSent = null;
        await offline.sync({ force: true });
        return;
      }
      const next = move.next_turn_player_indices;
      const seats = new Set(match.players.map(player=>player.player_index));
      if (!(next === null || (Array.isArray(next) && next.length && new Set(next).size === next.length && next.every(seat=>Number.isInteger(seat)&&seats.has(seat)))))
        throw new Error("The game sent an invalid next player. Your move was not queued. Restart or repair this game in Builder.");
      if (!match || !user || actingFor(match, user.id) === null)
        throw new Error("It isn't your turn.");
      state.match = offline.enqueue(match, {
        ...move,
        expected_move_count: match.move_count,
      });
      state.lastSent = null;
      await offline.sync({ force: true });
    });
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    state.moveInFlight = false;
    state.lastSent = null;
    await refresh();
  }
}

async function practice() {
  try {
    const game = state.games.find((g) => g.id === $("new-game").value);
    const full = await portalRequest(
      "GET",
      `/games/${game.id}/versions/${game.version}`,
    );
    if (full.server_code) {
      $("create-message").textContent =
        "Practice isn't available for server-side games: their logic runs on the server. Create a match instead.";
      return;
    }
    const match = offline.practice(full, $("practice-mode").value);
    state.gameCache.set(`${full.id}@${full.version}`, full);
    selectMatch(match.id);
  } catch (error) {
    $("create-message").textContent = error.message;
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
    const bet = Number($("match-stake").value);
    if (!Number.isSafeInteger(bet) || bet < 0) throw new Error("Invalid token stake");
    if (bet > 0 && game?.allowed_player_counts?.length === 1 && game.allowed_player_counts[0] === 1) throw new Error("Token stakes require a multiplayer match");
    match = await portalRequest("POST", "/matches", {
      user,
      body: {
        game_id: $("new-game").value,
        num_computer_opponents: 0,
        bet_per_player: Number($("match-stake").value),
        ...(window.PortalSpeed?.createFields($("match-speed").value) ?? {}),
      },
    });
    // Solo-only games have no lobby or second seat to wait for. Start the pinned
    // version returned by the server; if the catalog changed, leave Start available.
    if (game && game.version === match.game_version && game.allowed_player_counts.length === 1 && game.allowed_player_counts[0] === 1 && match.players.length === 1) {
      match = await portalRequest("POST", `/matches/${match.id}/start`, {user, body: {first_turn_player_indices: [0]}});
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

function shouldRestoreSavedMatch() {
  return !state.detailGameId && !location.hash;
}

async function main() {
  window.PortalMatchLayout?.init(() => {
    selectMatch(null);
    render();
    syncGame();
    if (!state.detailGameId) $("match-creator").open = true;
    (state.detailGameId ? document.querySelector(".back-to-games") : $("game-search"))?.focus({preventScroll: true});
  });
  Auth.renderMenu($("account-menu"));
  $("notify-toggle").addEventListener("click", toggleNotifications);
  document.addEventListener("i18n-changed", () => {
    // Texts the script built (badges, buttons, lists) are redrawn in the new language.
    try {
      Auth.renderMenu($("account-menu"));
      render();
      renderLibrary();
      updateMatchSetup();
    } catch (error) {
      console.warn("Couldn't redraw after a language change:", error);
    }
  });
  document.addEventListener("auth-changed", () => {
    Auth.renderMenu($("account-menu"));
    state.armed = null;
    state.lastSent = null;
    state.selectedId = null;
    state.match = null;
    state.myMatches = [];
    state.gameCache.clear();
    state.nameCache.clear();
    state.pictureCache.clear();
    frame.unload();
    state.loaded = null;
    history.replaceState(null, "", state.detailGameId ? `#game=${encodeURIComponent(state.detailGameId)}` : "#");
    showMessage("");
    refresh();
    setupNotifications();
  });
  $("practice").addEventListener("click", practice);
  $("retry-sync").addEventListener("click", () => {
    offline.retryAfter = 0;
    state.lastSent = null;
    refresh();
  });
  window.addEventListener("online", () => {
    offline.retryAfter = 0;
    refresh();
  });
  window.addEventListener("offline", () => {
    offlineStatus();
    refresh();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  $("match-creator").addEventListener("toggle", updateMatchSetup);
  window.addEventListener("storage", () => {
    state.lastSent = null;
    refresh();
  });
  $("new-game").addEventListener("change", updateMatchSetup);
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
  $("browse-games").addEventListener("click", browseAllGames);
  $("game-search").addEventListener("keydown", onGameSearchKey);
  $("game-chooser").addEventListener("focusout", (event) => {
    if (!$("game-chooser").contains(event.relatedTarget)) closeGameSearch();
  });
  document.addEventListener("click", (event) => {
    if (!$("game-chooser").contains(event.target)) closeGameSearch();
  });
  $("show-creator").addEventListener("click", () => {
    $("match-creator").open = true;
    $("match-creator").scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"});
    $("game-search").focus({preventScroll: true});
  });
  document.querySelectorAll("[data-game-filter]").forEach(button => button.addEventListener("click", () => {
    state.libraryFilter = button.dataset.gameFilter;
    document.querySelectorAll("[data-game-filter]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    renderLibrary();
    revealPortalSection("game-library");
  }));
  document.querySelectorAll("[data-game-view]").forEach(button => button.addEventListener("click", () => {
    state.libraryView = button.dataset.gameView;
    document.querySelectorAll("[data-game-view]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    renderLibrary();
    revealPortalSection("game-library");
  }));
  $("reset-games").addEventListener("click", () => {
    state.libraryFilter = "all";
    document.querySelectorAll("[data-game-filter]").forEach(item => item.setAttribute("aria-pressed", String(item.dataset.gameFilter === "all")));
    browseAllGames();
  });
  $("match-stake").addEventListener("change", updateStakePreview);
  $("create").addEventListener("click", createMatch);
  window.addEventListener("hashchange", selectFromHash);
  if (shouldRestoreSavedMatch() && offline.read().selectedId)
    history.replaceState(null, "", `#match=${offline.read().selectedId}`);
  const cached = offline.read();
  state.selectedId = new URLSearchParams(location.hash.slice(1)).get("match");
  state.games = cached.responses["/games"] || [];
  state.myMatches = [
    ...(cached.responses["/matches"] || []).map((m) => offline.decorate(m)),
    ...Object.values(cached.local),
  ];
  state.openMatches = cached.responses["/matches/open"] || [];
  for (const [path, value] of Object.entries(cached.responses)) {
    const game = /^\/games\/([^/]+)\/versions\/(\d+)$/.exec(path);
    if (game) state.gameCache.set(`${game[1]}@${game[2]}`, value);
    const user = /^\/users\/([^/]+)$/.exec(path);
    if (user) { state.nameCache.set(user[1], value.display_name); state.pictureCache.set(user[1], value.picture_url); }
  }
  state.match =
    cached.local[state.selectedId] ||
    cached.responses[`/matches/${state.selectedId}`] ||
    null;
  if (state.match && !state.match.localMode)
    state.match = offline.decorate(state.match);
  render();
  try {
    await syncGame();
  } catch (error) {
    showPageError(error.message);
  }
  refresh();
  setInterval(() => {
    if (!document.hidden && !state.moveInFlight) refresh();
  }, POLL_MS);
  setInterval(updateSpeedClock, 250);
  if ("serviceWorker" in navigator && !document.querySelector('meta[name="local-preview"]'))
    navigator.serviceWorker
      .register("/static/portal-sw.js")
      .then(async () => {
        // /static/portal.html is inside the worker's permitted scope; /portal isn't.
        if (location.pathname === "/portal") {
          const registration = await navigator.serviceWorker.getRegistration(
            "/static/portal.html",
          );
          const worker =
            registration?.installing ||
            registration?.waiting ||
            registration?.active;
          const enter = () =>
            location.replace(
              "/static/portal.html" + location.search + location.hash,
            );
          if (worker?.state === "activated") enter();
          else
            worker?.addEventListener("statechange", () => {
              if (worker.state === "activated") enter();
            });
        } else {
          await navigator.serviceWorker.ready;
          shellReady = true;
          offlineStatus();
        }
      })
      .catch((error) =>
        showPageError(`Offline setup failed: ${error.message}`),
      );
  setupNotifications();
}

$("history-back").addEventListener("click", () => browseHistory(-1));
$("history-forward").addEventListener("click", () => browseHistory(1));
$("history-live").addEventListener("click", () => browseHistory("live"));
main();
