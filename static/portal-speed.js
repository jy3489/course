"use strict";

// Speed matches on the match page: the per-move countdown and the "time ran out"
// messages. The server enforces the clock; this only shows it. Times come from the
// server (turn_deadline and server_time), so a phone whose clock is off still counts
// down correctly.
window.PortalSpeed = (() => {
  let offsetMs = 0; // the server's clock minus this device's clock
  let lastServerTime = null;

  /** Learns the server's clock from a fresh response. A cached copy (offline) repeats
   * its old server_time, so it doesn't move the estimate. */
  function observe(match, nowMs = Date.now()) {
    const serverTime = match?.server_time;
    if (!serverTime || serverTime === lastServerTime) return;
    lastServerTime = serverTime;
    offsetMs = Date.parse(serverTime) - nowMs;
  }

  /** Whole seconds until the human seats with the turn run out of time, or null when no
   * clock is running (a regular match, a computer's turn, not ongoing). */
  function secondsLeft(match, nowMs = Date.now()) {
    if (!match?.turn_seconds || !match.turn_deadline || match.status !== "ongoing") return null;
    return Math.max(0, Math.ceil((Date.parse(match.turn_deadline) - (nowMs + offsetMs)) / 1000));
  }

  /** "1:05" */
  function clockText(seconds) {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  /** What the countdown shows to [userId]: {text, urgent}, or null to hide it. */
  function clock(match, userId, nameOf, nowMs = Date.now()) {
    const left = secondsLeft(match, nowMs);
    if (left === null) return null;
    const turn = new Set(match.turn_of_player_indices ?? []);
    const humans = match.players.filter((p) => p.kind === "human" && turn.has(p.player_index));
    if (!humans.length) return null;
    if (userId && humans.some((p) => p.user_id === userId)) {
      return left <= 10
        ? { text: I18n.t("ui.speed.your_move_seconds", { seconds: left }, `Your move: ${left} s left`), urgent: true }
        : { text: I18n.t("ui.speed.your_move", { time: clockText(left) }, `Your move: ${clockText(left)}`), urgent: false };
    }
    const time = clockText(left);
    if (humans.length > 1) return { text: time, urgent: false };
    const name = nameOf(humans[0].user_id);
    return { text: I18n.t("ui.speed.their_move", { name, time }, `${name}: ${time}`), urgent: false };
  }

  /** The message about running out of time for [userId], or null. */
  function notice(match, userId, nameOf) {
    if (!match?.turn_seconds) return null;
    if (match.status === "over") {
      return match.end_reason === "timed_out" ? I18n.t("ui.speed.ended", {}, "Match ended: time ran out") : null;
    }
    const timeouts = match.timeouts ?? [];
    if (match.status !== "ongoing" || !timeouts.length) return null;
    const seated = match.players.some((p) => p.user_id === userId);
    if (userId && !seated && timeouts.some((t) => t.user_id === userId)) {
      return I18n.t("ui.speed.you_timed_out", {}, "You ran out of time. A computer is playing for you now.");
    }
    const last = timeouts[timeouts.length - 1];
    const name = nameOf(last.user_id);
    return I18n.t("ui.speed.took_over", { name }, `Time ran out: a computer took over for ${name}`);
  }

  /** The create form's choice as request fields: nothing for Regular. */
  function createFields(value) {
    const seconds = Number(value);
    return value && Number.isSafeInteger(seconds) ? { turn_seconds: seconds } : {};
  }

  return { observe, secondsLeft, clockText, clock, notice, createFields };
})();
