// Builder: create, edit, delete and test your games. Tests run the editor's code in a
// sandboxed iframe in pass-and-play mode, keeping the moves in this page.

"use strict";

const $ = (id) => document.getElementById(id);
const PLAYER_COUNTS = [2, 3, 4, 5, 6, 7, 8, 9, 10];

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
  selectedId: null, // a game id, "new", or null
  baseline: null, // the editor values when last loaded or saved
  pendingSwitch: null, // a selection waiting for a second click to discard edits
  deleteArmed: false,
  test: null,
  agent: null, // {messages: [{role, content}], pending: {code, name, description} | null}
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
  resetAgent();
  stopTest();
  await loadGames();
  render();
}

async function loadGames() {
  const user = me();
  state.games = user
    ? await apiRequest("GET", `/games?owner_user_id=${encodeURIComponent(user.id)}`)
    : [];
}

function renderGames() {
  const items = state.games.map((game) =>
    el(
      "button",
      {
        type: "button",
        class: "list-item",
        "aria-selected": String(game.id === state.selectedId),
        onclick: () => select(game.id),
      },
      el("div", { class: "title" }, game.name, el("span", { class: "badge", textContent: `v${game.version}` })),
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
        onchange: renderTestSetup,
      }),
      String(count)),
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
  resetAgent();
  const game = selectedGame();
  fillEditor(game ?? NEW_GAME);
  showTab(id === "new" ? "agent" : "edit");
  setMessage("edit-message", "");
  stopTest();
  render();
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
  for (const id of ["save", "agent-publish", "delete", "revert", "new-game"]) {
    $(id).disabled = state.saving;
  }
}

async function save() {
  if (state.saving) return;
  if (state.agent?.pending || state.agentSending) {
    saveMessage("Wait for the agent, then apply or discard its proposal before publishing.", "error");
    return;
  }
  const user = me();
  const selectedId = state.selectedId;
  if (!user || !selectedId) return;
  const values = editorValues();
  const baseline = JSON.stringify(values);
  state.saving = true;
  updateSaveControls();
  saveMessage("Saving…");
  try {
    const game = await apiRequest(selectedId === "new" ? "POST" : "PATCH",
      selectedId === "new" ? "/games" : `/games/${selectedId}`, { body: values, user });
    if (me()?.id !== user.id || state.selectedId !== selectedId) return;
    state.games = [game, ...state.games.filter((item) => item.id !== game.id)];
    state.selectedId = game.id;
    // Keep edits typed while the request was in flight.
    if (JSON.stringify(editorValues()) === baseline) fillEditor(game);
    else state.baseline = baseline;
    saveMessage(`Published version ${game.version}.`, "ok");
    render();
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
    setMessage("edit-message",
      "Click Delete again to delete this game. Existing matches keep working; new ones can't be created.", "error");
    return;
  }
  try {
    await apiRequest("DELETE", `/games/${state.selectedId}`, { user: me() });
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
  $("game-meta").textContent = game
    ? `Version ${game.version} · created ${formatTime(game.created_at)} · updated ${formatTime(game.updated_at)} · id ${game.id}`
    : "A new game. Save to create it.";
  $("save").textContent = state.selectedId === "new" ? "Create" : "Save";
  $("delete").textContent = state.selectedId === "new" ? "Discard" : "Delete";
}

// Tabs

const TABS = ["edit", "test", "agent"];

function showTab(name) {
  for (const tab of TABS) {
    $(`tab-${tab}`).setAttribute("aria-selected", String(name === tab));
    $(`${tab}-panel`).hidden = name !== tab;
  }
  if (name === "test") renderTestSetup();
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
  state.test = { players, moves: [], state: null, turn: [0], status: "ongoing", awaiting: false };
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
  state.agent = null;
  state.agentSending = false;
  $("agent-send").disabled = false;
  $("agent-log").replaceChildren();
  $("agent-proposal").hidden = true;
  $("agent-input").value = "";
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

async function requestAgentReply(user, body, isCurrent) {
  let reply = await apiRequest("POST", "/agent/chat", { user, body });
  // The local connected preview returns a job immediately so long generations
  // survive browser/proxy request deadlines. Production returns the reply directly.
  const started = Date.now();
  const deadline = started + 16 * 60 * 1000;
  while (reply?.preview_job_id) {
    if (!isCurrent()) throw new Error("Conversation changed.");
    if (Date.now() > deadline) throw new Error("Generation is taking too long. Please try again later.");
    const seconds = Math.floor((Date.now() - started) / 1000);
    setMessage("agent-message", `Muse is generating your game… ${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed. Larger games can take several minutes.`);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    reply = await apiRequest("GET", `/preview/jobs/${encodeURIComponent(reply.preview_job_id)}`, { user });
  }
  return reply;
}

async function sendAgentMessage() {
  const user = me();
  const input = $("agent-input").value.trim();
  if (!input || !user || state.agentSending || state.saving) return;
  const history = agentHistory();
  if (history.pending) {
    setMessage("agent-message", "Apply or discard the pending proposal first.", "error");
    return;
  }
  const values = editorValues();
  const isCurrent = () => state.agent === history && me()?.id === user.id;
  const messages = [...history.messages, { role: "user", content: input }];
  $("agent-input").value = "";
  agentLog("user", input);
  setMessage("agent-message", "The agent is thinking…");
  state.agentSending = true;
  $("agent-send").disabled = true;
  try {
    const reply = await requestAgentReply(user, {
        game_name: values.name,
        game_description: values.description,
        allowed_player_counts: values.allowed_player_counts,
        code: values.code,
        messages,
    }, isCurrent);
    if (!isCurrent()) return;
    history.messages.push({ role: "user", content: input });
    history.messages.push({ role: "assistant", content: reply.message });
    agentLogReply(reply);
    if (reply.code) {
      history.pending = { code: reply.code, name: reply.name, description: reply.description };
      $("agent-proposal").hidden = false;
      agentLog("system", "The agent proposed new code. Review it with Apply, or Discard it.");
    }
    setMessage("agent-message", "");
  } catch (error) {
    if (!isCurrent()) return;
    setMessage("agent-message", `The agent couldn't reply: ${error.message}`, "error");
    $("agent-input").value = input;
  } finally {
    if (isCurrent()) {
      state.agentSending = false;
      $("agent-send").disabled = false;
    }
  }
}

function applyAgentProposal() {
  const history = agentHistory();
  const pending = history.pending;
  if (!pending) return;
  if (pending.name && !$("name").value.trim()) $("name").value = pending.name;
  if (pending.description && !$("description").value.trim()) $("description").value = pending.description;
  $("code").value = pending.code;
  history.pending = null;
  $("agent-proposal").hidden = true;
  agentLog("system", "Applied the proposal. Test it in the Test tab, then keep chatting or publish.");
  setMessage("agent-message", "Applied. You have unsaved changes.", "ok");
}

function discardAgentProposal() {
  const history = agentHistory();
  history.pending = null;
  $("agent-proposal").hidden = true;
  agentLog("system", "Discarded the proposal.");
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
  if (showWorkspace) renderEditorMeta();
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
  $("save").addEventListener("click", save);
  $("revert").addEventListener("click", () => {
    fillEditor(selectedGame() ?? NEW_GAME);
    setMessage("edit-message", "Reverted.");
  });
  $("delete").addEventListener("click", deleteGame);
  $("tab-edit").addEventListener("click", () => showTab("edit"));
  $("tab-test").addEventListener("click", () => showTab("test"));
  $("tab-agent").addEventListener("click", () => showTab("agent"));
  $("agent-send").addEventListener("click", sendAgentMessage);
  $("agent-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) sendAgentMessage();
  });
  $("agent-apply").addEventListener("click", applyAgentProposal);
  $("agent-discard").addEventListener("click", discardAgentProposal);
  $("agent-debug").addEventListener("change", (event) => setAgentDebug(event.target.checked));
  $("agent-publish").addEventListener("click", save);
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
