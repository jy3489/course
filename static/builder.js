// Builder: create, edit, delete and test your games. Tests run the editor's code in a
// sandboxed iframe in pass-and-play mode, keeping the moves in this page.

"use strict";

const $ = (id) => document.getElementById(id);
const PLAYER_COUNTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

// A minimal game showing the game API, used as the starting point for new games.
const TEMPLATE_CODE = [
  "<!doctype html>",
  "<html>",
  "<body style=\"font-family: sans-serif; padding: 16px\">",
  "<h2>Race to 10</h2>",
  "<p id=\"status\">Waiting for the platform…</p>",
  "<p>Total: <b id=\"total\">0</b></p>",
  "<button id=\"add1\">+1</button> <button id=\"add2\">+2</button>",
  "<script>",
  "let latest = null; // the last state_changed message",
  "function add(n) {",
  "  const seat = latest.acting_for_player_index;",
  "  const total = (latest.state ? latest.state.total : 0) + n;",
  "  const won = total >= 10;",
  "  parent.postMessage({",
  "    type: \"make_move\",",
  "    new_state: { total, winner: won ? seat : null },",
  "    next_turn_player_indices: won ? null : [(seat + 1) % latest.players.length],",
  "  }, \"*\");",
  "}",
  "addEventListener(\"message\", (event) => {",
  "  if (event.source !== parent || !event.data || event.data.type !== \"state_changed\") return;",
  "  latest = event.data;",
  "  const state = latest.state || { total: 0, winner: null };",
  "  const acting = latest.status === \"ongoing\" ? latest.acting_for_player_index : null;",
  "  document.getElementById(\"total\").textContent = state.total;",
  "  document.getElementById(\"status\").textContent = state.winner !== null",
  "    ? latest.players[state.winner].name + \" wins!\"",
  "    : acting === null ? \"Waiting for another player…\" : latest.players[acting].name + \"'s turn\";",
  "  for (const id of [\"add1\", \"add2\"]) document.getElementById(id).disabled = acting === null;",
  "  // Computers' moves are computed by the game, for whichever client acts for them.",
  "  if (acting !== null && latest.players[acting].kind === \"computer\") {",
  "    setTimeout(() => add(1 + Math.floor(Math.random() * 2)), 500);",
  "  }",
  "});",
  "document.getElementById(\"add1\").onclick = () => add(1);",
  "document.getElementById(\"add2\").onclick = () => add(2);",
  "</" + "script>",
  "</body>",
  "</html>",
  "",
].join("\n");

const NEW_GAME = {
  name: "",
  description: "",
  allowed_player_counts: [2],
  allows_leave_mid_match: false,
  allows_join_mid_match: false,
  code: TEMPLATE_CODE,
};

const state = {
  games: [], // the acting user's games (latest versions)
  drafts: [], // private working copies, visible only to this user
  selectedId: null, // a game id, "new", or null
  baseline: null, // the editor values when last loaded or saved
  pendingSwitch: null, // a selection waiting for a second click to discard edits
  deleteArmed: false,
  discardDraftArmed: false,
  test: null,
  agent: null, // {messages: [{role, content}], pending: {code, name, description} | null}
  activeTab: "agent",
  agentRequest: null,
  starters: [],
  draftUndo: [],
  restoring: false,
  saving: false,
  agentSending: false,
  agentDebug: false, // show the agent's thinking and raw replies in the chat
};

const frame = new GameFrame(
  $("test-frame"),
  (move) => onTestMove(move),
  (problem) => testLog(problem, true),
);

// Acting user and the game list

function me() {
  return Auth.current();
}

async function onUserChanged() {
  state.selectedId = null;
  state.baseline = null;
  state.games = [];
  state.drafts = [];
  resetAgent();
  state.draftUndo = [];
  stopTest();
  render();
  await loadGames();
  render();
}

async function loadGames() {
  const user = me();
  const [games, drafts] = user ? await Promise.all([
    apiRequest("GET", `/games?owner_user_id=${encodeURIComponent(user.id)}`),
    apiRequest("GET", "/game-drafts", {user}),
  ]) : [[], []];
  if (me()?.id !== user?.id) return;
  state.games = games;
  state.drafts = drafts;
}

function renderGames() {
  const library = new Map(state.games.map((game) => [game.id, game]));
  for (const draft of state.drafts) library.set(draft.id, draft);
  const items = [...library.values()].map((game) =>
    el(
      "button",
      {
        type: "button",
        class: "list-item",
        "aria-selected": String(game.id === state.selectedId),
        onclick: () => select(game.id),
      },
      el("div", { class: "title" }, game.name || "Untitled game", el("span", { class: "badge", textContent: game.version ? `Public · v${game.version}` : game.base_version ? `Private draft · public v${game.base_version}` : "Private draft" })),
      el("div", {
        class: "sub",
        textContent: `${game.allowed_player_counts.join(", ")} players · updated ${formatTime(game.updated_at)}`,
      }),
    ),
  );
  if (state.selectedId === "new") {
    items.push(el("button", { type: "button", class: "list-item", "aria-selected": "true" },
      el("div", { class: "title" }, "New game (unsaved)")));
  }
  if (!items.length) {
    items.push(el("div", { class: "empty-note", textContent: me() ? "No games yet. Create one with New game." : "Log in first." }));
  }
  $("games").replaceChildren(...items);
}

// Editor

function selectedGame() {
  return state.games.find((game) => game.id === state.selectedId) ?? null;
}

function selectedDraft() {
  return state.drafts.find((draft) => draft.id === state.selectedId) ?? null;
}

function editorValues() {
  return {
    name: $("name").value,
    description: $("description").value,
    allowed_player_counts: PLAYER_COUNTS.filter((count) => $(`count-${count}`).checked),
    allows_leave_mid_match: $("allows-leave").checked,
    allows_join_mid_match: $("allows-join").checked,
    code: $("code").value,
  };
}

function fillEditor(values) {
  $("name").value = values.name;
  $("description").value = values.description;
  $("counts").replaceChildren(...PLAYER_COUNTS.map((count) =>
    el("label", { class: "inline" },
      el("input", {
        type: "checkbox",
        id: `count-${count}`,
        checked: values.allowed_player_counts.includes(count),
        onchange: () => { renderTestSetup(); renderDraftControls(); },
      }),
      count === 1 ? "1 (solo)" : String(count)),
  ));
  $("allows-leave").checked = values.allows_leave_mid_match;
  $("allows-join").checked = values.allows_join_mid_match;
  $("code").value = values.code;
  state.baseline = JSON.stringify(editorValues());
}

const isDirty = () => state.baseline !== null && JSON.stringify(editorValues()) !== state.baseline;

function select(id) {
  if (id === state.selectedId || state.saving) return;
  if (id !== state.selectedId && (isDirty() || state.agent?.pending) && state.pendingSwitch !== id) {
    state.pendingSwitch = id;
    saveMessage("You have unsaved changes or an unapplied proposal. Click again to discard them.", "error");
    setMessage("test-status", "You have unsaved changes or an unapplied proposal. Click again to discard them.", "error");
    return;
  }
  state.pendingSwitch = null;
  state.selectedId = id;
  state.deleteArmed = false;
  state.discardDraftArmed = false;
  resetAgent();
  state.draftUndo = [];
  const game = selectedGame();
  fillEditor(selectedDraft() ?? game ?? NEW_GAME);
  stopTest();
  showTab(id === "new" ? "agent" : "edit");
  setMessage("edit-message", "");
  render();
  const working = selectedDraft() ?? game;
  if (working) {
    setTestMode(working.allowed_player_counts.some((count) => count >= 2) ? "computer" : "solo");
    startTest();
  }
}

function setMessage(id, text, kind = "") {
  $(id).textContent = text;
  $(id).className = `message ${kind}`;
}

function saveMessage(text, kind = "") {
  setMessage("edit-message", text, kind);
  setMessage("agent-message", text, kind);
}

function updateSaveControls() {
  for (const id of ["save", "agent-save", "publish", "agent-publish", "discard-draft", "delete", "revert", "new-game"]) {
    $(id).disabled = state.saving;
  }
  renderDraftControls();
}

async function save(publishing = false) {
  if (state.saving || state.restoring) return;
  if (state.agent?.pending || state.agentSending) {
    saveMessage("Wait for the agent, then apply or discard its proposal before saving or publishing.", "error");
    return;
  }
  const user = me();
  const selectedId = state.selectedId;
  if (!user || !selectedId) return;
  const values = editorValues();
  const baseline = JSON.stringify(values);
  const existingDraft = selectedDraft();
  state.saving = true;
  updateSaveControls();
  saveMessage(publishing ? "Saving privately, then publishing…" : "Saving private draft…");
  try {
    const draft = await apiRequest(selectedId === "new" ? "POST" : "PUT",
      selectedId === "new" ? "/game-drafts" : `/game-drafts/${encodeURIComponent(selectedId)}`,
      {body: {...values, expected_revision: existingDraft?.revision ?? null}, user});
    if (me()?.id !== user.id || state.selectedId !== selectedId) return;
    state.drafts = [draft, ...state.drafts.filter((item) => item.id !== draft.id)];
    state.selectedId = draft.id;
    // Keep edits typed while the request was in flight.
    if (JSON.stringify(editorValues()) === baseline) fillEditor(draft);
    else state.baseline = baseline;
    saveMessage("Saved privately. Only you can access this draft; Publish makes it public.", "ok");
    render();
    if (publishing) {
      try {
        const game = await apiRequest("POST", `/game-drafts/${encodeURIComponent(draft.id)}/publish`,
          {body: {expected_revision: draft.revision}, user});
        if (me()?.id !== user.id || state.selectedId !== draft.id) return;
        state.games = [game, ...state.games.filter((item) => item.id !== game.id)];
        state.drafts = state.drafts.map((item) => item.id === draft.id ? {...item, base_version: game.version} : item);
        saveMessage(`Published version ${game.version}. Everyone can now play this version.`, "ok");
      } catch (error) {
        if (me()?.id === user.id && state.selectedId === draft.id) {
          saveMessage(`Draft saved privately, but publishing failed: ${error.message}`, "error");
        }
      }
    }
  } catch (error) {
    if (me()?.id === user.id && state.selectedId === selectedId) {
      saveMessage(`Couldn't save: ${error.message}`, "error");
    }
  } finally {
    state.saving = false;
    updateSaveControls();
    render();
  }
}

async function discardSavedDraft() {
  if (!selectedDraft() || state.saving || state.restoring || state.agentSending || state.agent?.pending) return;
  const user = me(), id = state.selectedId;
  if (!state.discardDraftArmed) {
    state.discardDraftArmed = true;
    saveMessage("Click Discard private draft again to remove your saved draft. The public version stays available.", "error");
    return;
  }
  state.saving = true;
  updateSaveControls();
  try {
    await apiRequest("DELETE", `/game-drafts/${encodeURIComponent(id)}`, {user});
    if (me()?.id !== user.id || state.selectedId !== id) return;
    state.drafts = state.drafts.filter((item) => item.id !== id);
    fillEditor(selectedGame() ?? NEW_GAME);
    state.discardDraftArmed = false;
    resetAgent();
    state.draftUndo = [];
    stopTest();
    saveMessage("Private draft discarded. Public version loaded.", "ok");
  } catch (error) {
    saveMessage(`Couldn't discard draft: ${error.message}`, "error");
  } finally {
    state.saving = false;
    updateSaveControls();
    render();
  }
}

async function deleteGame() {
  if (state.saving) return;
  if (state.selectedId === "new") {
    stopTest();
    state.selectedId = null;
    state.baseline = null;
    render();
    return;
  }
  if (!state.deleteArmed) {
    state.deleteArmed = true;
    setMessage("edit-message", selectedGame()
      ? "Click Delete again to delete the public game and your draft. Existing matches keep working; new ones can't be created."
      : "Click Delete again to delete this private draft. It has never been published.", "error");
    return;
  }
  try {
    await apiRequest("DELETE", `${selectedGame() ? "/games" : "/game-drafts"}/${encodeURIComponent(state.selectedId)}`, { user: me() });
    stopTest();
    state.selectedId = null;
    state.baseline = null;
    state.deleteArmed = false;
    await loadGames();
    render();
  } catch (error) {
    setMessage("edit-message", `Couldn't delete: ${error.message}`, "error");
  }
}

function renderEditorMeta() {
  const game = selectedGame();
  const draft = selectedDraft();
  $("game-meta").textContent = game
    ? `Public version ${game.version}${draft ? " · private working copy" : ""} · id ${game.id}`
    : draft ? "Private draft · visible only to your account" : "Unsaved draft · Save keeps it private, Publish makes it public";
  $("save").textContent = "Save draft";
  $("publish").textContent = game ? "Publish new version" : "Publish game";
  $("agent-publish").textContent = $("publish").textContent;
  $("revert").textContent = draft ? "Revert to saved draft" : "Revert to published";
  $("discard-draft").hidden = !draft || !game;
  $("delete").textContent = state.selectedId === "new" ? "Discard" : "Delete";
}

// Tabs

const TABS = ["edit", "test", "agent"];

function showTab(name) {
  state.activeTab = name;
  for (const tab of TABS) {
    $(`tab-${tab}`).setAttribute("aria-selected", String(name === tab));
    $(`${tab}-panel`).hidden = name !== tab;
  }
  const chatHost = $(name === "edit" ? "edit-chat-host" : "agent-home");
  if ($("agent-tools").parentNode !== chatHost) chatHost.append($("agent-tools"));
  const testHost = $(name === "edit" ? "edit-test-host" : "test-home");
  if ($("test-tools").parentNode !== testHost) {
    testHost.append($("test-tools"));
    // Moving an iframe reloads its document in some browsers. Re-send the
    // stored match snapshot rather than losing the current test's progress.
    if (state.test) {
      frame.load(state.test.code ?? $("code").value);
      sendTestState();
    }
  }
  $("agent-send").textContent = name === "edit" ? "Update game ↗" : "Let’s build ↗";
  if (name === "test" || name === "edit") renderTestSetup();
  renderDraftControls();
}

function renderDraftControls() {
  $("undo-ai-change").disabled = state.draftUndo.length === 0 || state.agentSending || Boolean(state.agent?.pending) || state.saving || state.restoring;
  const game = selectedGame();
  const draft = selectedDraft();
  const sameAsPublic = game && draft && Object.keys(editorValues()).every((key) => JSON.stringify(game[key]) === JSON.stringify(draft[key]));
  $("draft-status").textContent = state.agent?.pending ? "Proposal awaiting review" : isDirty() ? "Unsaved changes — Save keeps them private" : sameAsPublic ? `Published v${game.version} — draft also saved` : draft ? "Draft saved privately — Publish shares it with everyone" : game ? "Published version loaded" : "New game — not saved or published";
  const versions = game ? Array.from({length: Math.min(game.version, 6)}, (_, i) => game.version - i) : [];
  $("published-versions").replaceChildren(...versions.map((version) => el("button", {
    type: "button", textContent: `Load v${version}${version === game.version ? " (latest)" : ""}`,
    disabled: state.saving || state.agentSending || state.restoring || Boolean(state.agent?.pending),
    onclick: () => restorePublishedVersion(version),
  })));
  if (!game) $("published-versions").textContent = "Publish your first version to keep its history.";
}

function replaceDraft(values) {
  const baseline = state.baseline;
  state.draftUndo.push(editorValues());
  fillEditor(values);
  state.baseline = baseline;
  stopTest();
  renderTestSetup();
  renderDraftControls();
}

function undoAppliedChange() {
  if (state.saving || state.agentSending || state.agent?.pending || state.restoring || !state.draftUndo.length) return;
  const baseline = state.baseline;
  fillEditor(state.draftUndo.pop());
  state.baseline = baseline;
  stopTest();
  renderTestSetup();
  renderDraftControls();
  if (state.activeTab === "edit" || state.activeTab === "test") startTest();
  saveMessage("Restored the previous draft. Save privately or Publish to share it.", "ok");
}

async function restorePublishedVersion(version) {
  const user = me();
  const id = state.selectedId;
  if (!user || !selectedGame() || state.saving || state.agentSending || state.agent?.pending || state.restoring) return;
  const snapshot = JSON.stringify(editorValues());
  state.restoring = true;
  renderDraftControls();
  try {
    const game = await apiRequest("GET", `/games/${encodeURIComponent(id)}/versions/${version}`, {user});
    if (state.selectedId !== id || me()?.id !== user.id) return;
    if (JSON.stringify(editorValues()) !== snapshot) {
      saveMessage("The draft changed while the version loaded. Click Load again to try again.", "error");
      return;
    }
    replaceDraft(game);
    if (state.activeTab === "edit" || state.activeTab === "test") startTest();
    saveMessage(`Loaded version ${version} as a draft. Publish to use it for new matches.`, "ok");
  } catch (error) {
    if (state.selectedId === id && me()?.id === user.id) saveMessage(`Couldn't load version: ${error.message}`, "error");
  } finally {
    state.restoring = false;
    renderDraftControls();
  }
}

// Pass-and-play test

function testLog(text, isError = false) {
  $("test-log").prepend(el("div", { class: `entry${isError ? " error" : ""}`, textContent: text }));
}

function renderTestSetup() {
  const counts = PLAYER_COUNTS.filter((count) => $(`count-${count}`)?.checked);
  const select = $("test-count");
  const previous = Number(select.value);
  select.replaceChildren(...counts.map((count) => el("option", { value: count, textContent: count })));
  if (counts.includes(previous)) select.value = String(previous);
  renderSeatKinds();
  $("test-mode-solo").disabled = !counts.includes(1);
  $("test-mode-computer").disabled = !counts.some((count) => count >= 2);
  $("test-mode-human").disabled = !counts.some((count) => count >= 2);
}

function setTestMode(mode) {
  const counts = PLAYER_COUNTS.filter((count) => $(`count-${count}`)?.checked);
  const count = mode === "solo" ? (counts.includes(1) ? 1 : null) : counts.find((count) => count >= 2);
  if (!count) return;
  $("test-count").value = String(count);
  renderSeatKinds();
  [...$("test-seats").querySelectorAll("select")].forEach((select, seat) => {
    select.value = mode === "computer" && seat > 0 ? "computer" : "human";
  });
}

function renderSeatKinds() {
  const count = Number($("test-count").value) || 0;
  const previous = [...$("test-seats").querySelectorAll("select")].map((s) => s.value);
  $("test-seats").replaceChildren(...[...Array(count).keys()].map((seat) => {
    const kind = el("select", { "aria-label": `Seat ${seat}` },
      el("option", { value: "human", textContent: `${seat}: human` }),
      el("option", { value: "computer", textContent: `${seat}: computer` }));
    kind.value = previous[seat] ?? "human";
    return kind;
  }));
}

function startTest() {
  const kinds = [...$("test-seats").querySelectorAll("select")].map((s) => s.value);
  if (kinds.length === 0) {
    setMessage("test-status", "Choose at least one player count first.", "error");
    return;
  }
  const players = kinds.map((kind, seat) => ({
    player_index: seat,
    kind,
    name: kind === "human" ? `Player ${seat + 1}` : `Computer ${seat + 1}`,
  }));
  state.test = { code: $("code").value, players, moves: [], state: null, turn: [0], status: "ongoing", awaiting: false };
  $("test-log").replaceChildren();
  frame.load($("code").value);
  testLog(`Started a test with ${players.length} players.`);
  sendTestState();
}

function stopTest() {
  state.test = null;
  frame.unload();
  $("test-log").replaceChildren();
  setMessage("test-status", "");
}

function sendTestState() {
  const test = state.test;
  const firstTurn = test.turn === null ? null : test.turn[0];
  const turnSeat = firstTurn === null ? null : test.players[firstTurn];
  const message = stateChangedMessage({
    state: test.state,
    players: test.players,
    turn: test.turn,
    status: test.status,
    endReason: test.status === "over" ? "finished" : null,
    moveCount: test.moves.length,
    // In pass-and-play the person at the screen is whichever human has the turn.
    mySeat: turnSeat && turnSeat.kind === "human" ? firstTurn : null,
    actingFor: test.status === "ongoing" ? firstTurn : null,
  });
  test.awaiting = test.status === "ongoing";
  frame.send(message);
  setMessage("test-status",
    test.status === "over"
      ? `The game ended after ${test.moves.length} moves.`
      : `Move ${test.moves.length + 1}: ${test.turn.map((seat) => test.players[seat].name).join(", ")}'s turn.`,
    test.status === "over" ? "ok" : "");
  testLog(`sent state_changed: move_count=${test.moves.length} turn=${JSON.stringify(test.turn)} status=${test.status}`);
}

function onTestMove(move) {
  const test = state.test;
  if (!test || !test.awaiting) {
    testLog("ignored make_move: no move is expected now (the game already moved, or it's over)", true);
    return;
  }
  const next = move.next_turn_player_indices;
  const valid = next === null ||
    (Array.isArray(next) && next.length > 0 && new Set(next).size === next.length &&
      next.every((seat) => Number.isInteger(seat) && seat >= 0 && seat < test.players.length));
  if (!valid) {
    testLog(`rejected make_move: next_turn_player_indices ${JSON.stringify(next)} is not a set of seats`, true);
    sendTestState();
    return;
  }
  test.awaiting = false;
  test.moves.push({ seats: test.turn, new_state: move.new_state, next });
  testLog(`received make_move from seats ${JSON.stringify(test.turn)}: next=${JSON.stringify(next)} new_state=${JSON.stringify(move.new_state)}`);
  test.state = move.new_state;
  test.turn = next;
  test.status = next === null ? "over" : "ongoing";
  sendTestState();
}

function undoTestMove() {
  const test = state.test;
  if (!test || test.moves.length === 0) return;
  test.moves.pop();
  const last = test.moves[test.moves.length - 1];
  test.state = last ? last.new_state : null;
  test.turn = last ? last.next : [0];
  test.status = "ongoing";
  testLog("undid the last move");
  sendTestState();
}

// Game-building agent: iterative chat that proposes code for the editor.

function resetAgent() {
  if (state.agentRequest) {
    state.agentRequest.cancelled = true;
    state.agentRequest.controller.abort();
  }
  state.agentRequest = null;
  $("agent-activity").hidden = true;
  $("agent-stop").hidden = true;
  state.agent = null;
  state.agentSending = false;
  $("agent-send").disabled = false;
  $("agent-log").replaceChildren();
  $("agent-proposal").hidden = true;
  $("agent-input").value = "";
  $("agent-proposed-code").textContent = "";
  setMessage("agent-message", "");
}

function agentLog(role, text) {
  $("agent-log").append(el("div", { class: `msg ${role}`, textContent: text }));
  $("agent-log").scrollTop = $("agent-log").scrollHeight;
}

/** One collapsible debug section inside an assistant message. */
function agentDebugSection(title, text) {
  return el(
    "details",
    { class: "agent-debug", open: state.agentDebug || undefined },
    el("summary", {}, title),
    el("pre", {}, text)
  );
}

/** Render an assistant reply as a chat bubble with optional debug details. */
function agentLogReply(reply) {
  const log = $("agent-log");
  const bubble = el("div", { class: "msg assistant" }, reply.message);
  if (reply.thinking) bubble.append(agentDebugSection("💭 Thinking", reply.thinking));
  if (reply.raw) bubble.append(agentDebugSection("🧾 Raw response", reply.raw));
  log.append(bubble);
  if (reply.parse_note) {
    const attempts = reply.attempts > 1 ? `took ${reply.attempts} attempts` : "needed a retry";
    log.append(
      el("div", {
        class: "msg system agent-note",
        textContent: `The agent ${attempts} (${reply.parse_note}).`,
      })
    );
  }
  log.scrollTop = log.scrollHeight;
}

/** The Debug toggle expands or collapses every thinking/raw section at once. */
function setAgentDebug(on) {
  state.agentDebug = on;
  for (const details of $("agent-log").querySelectorAll("details.agent-debug")) {
    details.open = on;
  }
}

function agentHistory() {
  if (!state.agent) state.agent = { messages: [], pending: null };
  return state.agent;
}

function generationActivity(phase, started) {
  $("agent-activity").hidden = false;
  if ($("agent-phase").textContent !== phase) $("agent-phase").textContent = phase;
  const seconds = Math.floor((Date.now() - started) / 1000);
  $("agent-elapsed").textContent = `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed`;
  $("agent-wait-note").textContent = seconds >= 60
    ? "The live server has not returned game code. Its model progress is unavailable. You can keep waiting or stop waiting."
    : "Waiting for the live course server to return a proposal. This indicator is not model progress.";
}

function stopAgentWaiting() {
  const request = state.agentRequest;
  if (!request) return;
  request.cancelled = true;
  request.controller.abort();
  setMessage("agent-message", "Stopping this browser's wait…");
}

async function requestAgentReply(user, body, isCurrent, request) {
  const started = Date.now();
  const options = {user, signal: request.controller.signal};
  let phase = "Sending request to the course server";
  generationActivity(phase, started);
  const ticker = setInterval(() => {
    if (isCurrent() && !request.cancelled) generationActivity(phase, started);
  }, 1000);
  try {
    let reply = await apiRequest("POST", "/agent/chat", {...options, body});
    const deadline = started + 16 * 60 * 1000;
    while (reply?.preview_job_id) {
      if (!isCurrent() || request.cancelled) throw new Error("Waiting stopped.");
      if (Date.now() > deadline) throw new Error("Generation is taking too long. Please try again later.");
      phase = "Request accepted — waiting for the backend response";
      generationActivity(phase, started);
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (request.cancelled) throw new Error("Waiting stopped.");
      reply = await apiRequest("GET", `/preview/jobs/${encodeURIComponent(reply.preview_job_id)}`, options);
    }
    if (request.cancelled) throw new Error("Waiting stopped.");
    return reply;
  } finally {
    clearInterval(ticker);
  }
}

async function loadStarters() {
  $("load-starters").disabled = true;
  try {
    state.starters = await apiRequest("GET", "/games");
    $("starter-game").replaceChildren(...state.starters.map((game) => el("option", {value: game.id, textContent: `${game.name} · v${game.version}`})));
    $("use-starter").disabled = state.starters.length === 0;
  } catch (error) {
    setMessage("agent-message", `Couldn't load the catalog: ${error.message}`, "error");
  } finally {
    $("load-starters").disabled = false;
  }
}

function useStarter() {
  if (state.selectedId !== "new" || state.saving || state.agentSending || state.agent?.pending || state.restoring) {
    setMessage("agent-message", "Stop waiting and apply or discard any proposal before loading a starting game.", "error");
    return;
  }
  const game = state.starters.find((game) => game.id === $("starter-game").value);
  if (!game) return;
  resetAgent();
  replaceDraft({...game, name: `${game.name} (copy)`, description: `${game.description || game.name} · Based on ${game.name} v${game.version}.`});
  showTab("edit");
  setTestMode(game.allowed_player_counts.some((count) => count >= 2) ? "computer" : "solo");
  startTest();
  agentLog("system", `Loaded a copy of published ${game.name} v${game.version}. No AI generation was performed. Describe a change to customize it.`);
  saveMessage("Loaded an unpublished copy. Customize it and test before publishing.", "ok");
}

async function sendAgentMessage() {
  const user = me();
  const input = $("agent-input").value.trim();
  if (!input || !user || state.agentSending || state.saving || state.restoring) return;
  const history = agentHistory();
  if (history.pending) {
    setMessage("agent-message", "Apply or discard the pending proposal first.", "error");
    return;
  }
  const values = editorValues();
  const request = {cancelled: false, controller: new AbortController()};
  state.agentRequest = request;
  const isCurrent = () => state.agent === history && me()?.id === user.id && state.agentRequest === request;
  const messages = [...history.messages, { role: "user", content: input }];
  $("agent-input").value = "";
  agentLog("user", input);
  setMessage("agent-message", "");
  $("agent-stop").hidden = false;
  state.agentSending = true;
  renderDraftControls();
  $("agent-send").disabled = true;
  try {
    const reply = await requestAgentReply(user, {
        game_name: values.name,
        game_description: values.description,
        allowed_player_counts: values.allowed_player_counts,
        code: values.code,
        messages,
    }, isCurrent, request);
    if (!isCurrent() || request.cancelled) return;
    history.messages.push({ role: "user", content: input });
    history.messages.push({ role: "assistant", content: reply.message });
    agentLogReply(reply);
    if (reply.code) {
      history.pending = { code: reply.code, name: reply.name, description: reply.description, base: JSON.stringify(values) };
      $("agent-proposed-code").textContent = reply.code;
      $("agent-proposal").hidden = false;
      agentLog("system", "The agent proposed new code. Review it with Apply, or Discard it.");
    }
    setMessage("agent-message", "");
  } catch (error) {
    if (!isCurrent()) return;
    setMessage("agent-message", request.cancelled
      ? "Stopped waiting. The live server may still finish the original request. Your prompt is preserved."
      : `The agent couldn't reply: ${error.message}`, request.cancelled ? "" : "error");
    if (!$("agent-input").value.trim()) $("agent-input").value = input;
  } finally {
    if (isCurrent()) {
      state.agentSending = false;
      state.agentRequest = null;
      $("agent-activity").hidden = true;
      $("agent-stop").hidden = true;
      $("agent-send").disabled = false;
      renderDraftControls();
    }
  }
}

function applyAgentProposal() {
  const history = agentHistory();
  const pending = history.pending;
  if (!pending || state.saving || state.restoring) return;
  if (pending.base && JSON.stringify(editorValues()) !== pending.base) {
    setMessage("agent-message", "The draft changed while Muse was working. Discard this proposal and request a fresh change to keep your edits.", "error");
    return;
  }
  replaceDraft({...editorValues(), code: pending.code,
    name: pending.name || $("name").value, description: pending.description || $("description").value});
  history.pending = null;
  $("agent-proposal").hidden = true;
  $("agent-proposed-code").textContent = "";
  showTab("edit");
  startTest();
  agentLog("system", "Applied to the draft and restarted the preview. Save privately, ask for another change, or publish when ready.");
  saveMessage("Applied to draft. Save to keep it private, or Publish to share it.", "ok");
  renderDraftControls();
}

function discardAgentProposal() {
  const history = agentHistory();
  history.pending = null;
  $("agent-proposal").hidden = true;
  agentLog("system", "Discarded the proposal.");
  $("agent-proposed-code").textContent = "";
  renderDraftControls();
}

// Rendering

function render() {
  renderGames();
  const showWorkspace = Boolean(me()) && state.selectedId !== null;
  $("workspace").hidden = !showWorkspace;
  $("no-user").hidden = showWorkspace;
  if (!me()) {
    $("no-user").replaceChildren(
      "Log in at the top right to see and build your games. ",
      el("button", { type: "button", class: "link", textContent: "Log in", onclick: () => Auth.openDialog("login") })
    );
  } else {
    $("no-user").replaceChildren(
      "Ready to make your next favorite game? ",
      el("button", { type: "button", textContent: "Create a game ↗", onclick: () => select("new") })
    );
  }
  $("new-game").disabled = !me() || state.saving;
  $("starter-options").hidden = state.selectedId !== "new";
  if (showWorkspace) { renderEditorMeta(); renderDraftControls(); }
  $("play-game").hidden = !selectedGame();
  if (selectedGame()) $("play-game").href = `/portal#game=${encodeURIComponent(state.selectedId)}`;
}

function protectUnsavedChanges(event) {
  if (isDirty() || state.agent?.pending || state.agentSending || state.saving) {
    event.preventDefault();
    event.returnValue = "";
  }
}

async function main() {
  window.addEventListener("beforeunload", protectUnsavedChanges);
  $("new-game").addEventListener("click", () => select("new"));
  $("save").addEventListener("click", () => save());
  $("agent-save").addEventListener("click", () => save());
  $("publish").addEventListener("click", () => save(true));
  $("discard-draft").addEventListener("click", discardSavedDraft);
  $("undo-ai-change").addEventListener("click", undoAppliedChange);
  for (const id of ["name", "description", "code", "allows-leave", "allows-join"]) {
    $(id).addEventListener("input", renderDraftControls);
  }
  $("revert").addEventListener("click", () => {
    fillEditor(selectedDraft() ?? selectedGame() ?? NEW_GAME);
    stopTest();
    renderTestSetup();
    renderDraftControls();
    setMessage("edit-message", "Reverted.");
  });
  $("delete").addEventListener("click", deleteGame);
  $("tab-edit").addEventListener("click", () => showTab("edit"));
  $("tab-test").addEventListener("click", () => showTab("test"));
  $("tab-agent").addEventListener("click", () => showTab("agent"));
  $("agent-send").addEventListener("click", sendAgentMessage);
  $("agent-stop").addEventListener("click", stopAgentWaiting);
  $("load-starters").addEventListener("click", loadStarters);
  $("use-starter").addEventListener("click", useStarter);
  $("agent-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) sendAgentMessage();
  });
  $("agent-apply").addEventListener("click", applyAgentProposal);
  $("agent-discard").addEventListener("click", discardAgentProposal);
  $("agent-debug").addEventListener("change", (event) => setAgentDebug(event.target.checked));
  $("agent-publish").addEventListener("click", () => save(true));
  for (const [id, mode] of [["test-mode-solo", "solo"], ["test-mode-computer", "computer"], ["test-mode-human", "human"]]) {
    $(id).addEventListener("click", () => setTestMode(mode));
  }
  $("test-count").addEventListener("change", renderSeatKinds);
  $("test-start").addEventListener("click", startTest);
  $("test-undo").addEventListener("click", undoTestMove);
  Auth.renderMenu($("account-menu"));
  document.addEventListener("auth-changed", async () => {
    Auth.renderMenu($("account-menu"));
    await onUserChanged();
  });
  try {
    await loadGames();
  } catch (error) {
    showPageError(`Couldn't load data from the server: ${error.message}`);
    return;
  }
  render();
}

main();
