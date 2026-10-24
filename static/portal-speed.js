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

  // The last seconds of a turn: the countdown turns red for everyone, and the player
  // to move gets the alert (see createAlerter).
  const URGENT_SECONDS = 10;

  /** Whether it's [userId]'s turn (a human seat of theirs is in the turn). */
  function isMyTurn(match, userId) {
    const turn = new Set(match?.turn_of_player_indices ?? []);
    return Boolean(userId) && (match?.players ?? []).some((p) => p.user_id === userId && turn.has(p.player_index));
  }

  /** What the countdown shows to [userId]: {text, urgent, mine}, or null to hide it.
   * In the last 10 seconds it's urgent (red) for everyone in the match. */
  function clock(match, userId, nameOf, nowMs = Date.now()) {
    const left = secondsLeft(match, nowMs);
    if (left === null) return null;
    const turn = new Set(match.turn_of_player_indices ?? []);
    const humans = match.players.filter((p) => p.kind === "human" && turn.has(p.player_index));
    if (!humans.length) return null;
    const time = clockText(left);
    const urgent = left <= URGENT_SECONDS;
    if (isMyTurn(match, userId)) {
      return { text: I18n.t("ui.speed.your_move", { time }, `Your move: ${time}`), urgent, mine: true };
    }
    if (humans.length > 1) return { text: time, urgent, mine: false };
    const name = nameOf(humans[0].user_id);
    return { text: I18n.t("ui.speed.their_move", { name, time }, `${name}: ${time}`), urgent, mine: false };
  }

  /** The 10-second alert for the player to move, in their browser only: once per turn a
   * short sound (if the page was interacted with), a short vibration, a browser
   * notification when the page is hidden and permission was already granted (never
   * asked for here), and a flashing tab title until they move or the time runs out.
   * [env] holds the browser APIs, so tests can replace them. */
  function createAlerter(env) {
    let alertedFor = null; // the turn (match + deadline) already alerted
    let flashing = null; // {timer, title}
    let interacted = false;
    let audio = null;
    for (const type of ["pointerdown", "keydown", "touchstart"]) {
      env.window?.addEventListener?.(type, () => { interacted = true; }, { once: true, capture: true });
    }

    function stopFlash() {
      if (!flashing) return;
      env.clearInterval(flashing.timer);
      env.document.title = flashing.title;
      flashing = null;
    }

    function startFlash() {
      const title = env.document.title;
      const alertTitle = I18n.t("ui.speed.title_alert", {}, "⏰ 10 s left!");
      let on = true;
      env.document.title = alertTitle;
      const timer = env.setInterval(() => {
        on = !on;
        env.document.title = on ? alertTitle : title;
      }, 1000);
      flashing = { timer, title };
    }

    function beep() {
      const hasBeenActive = env.navigator?.userActivation?.hasBeenActive ?? interacted;
      if (!hasBeenActive || !env.AudioContext) return;
      try {
        audio ??= new env.AudioContext();
        audio.resume?.();
        const tone = audio.createOscillator(), gain = audio.createGain(), t = audio.currentTime;
        tone.frequency.value = 880;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.15, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
        tone.connect(gain).connect(audio.destination);
        tone.start(t);
        tone.stop(t + 0.3);
      } catch {}
    }

    function notify(match, gameName) {
      if (!env.document.hidden || env.Notification?.permission !== "granted") return;
      const body = I18n.t("ui.speed.notify_ten_seconds", { game: gameName }, `Your move in ${gameName}: 10 seconds left!`);
      const options = { body, tag: `speed-${match.id}`, data: { url: `/static/portal.html#match=${match.id}` } };
      Promise.resolve(env.navigator?.serviceWorker?.ready)
        .then((registration) => registration ? registration.showNotification(gameName, options) : new env.Notification(gameName, options))
        .catch(() => {});
    }

    /** Called with the current match on every countdown tick. */
    function update(match, userId, gameName, nowMs = Date.now()) {
      const left = secondsLeft(match, nowMs);
      const urgentForMe = left !== null && left > 0 && left <= URGENT_SECONDS && isMyTurn(match, userId);
      if (!urgentForMe) { stopFlash(); return; }
      const key = `${match.id}@${match.turn_deadline}`;
      if (alertedFor === key) return;
      alertedFor = key;
      stopFlash();
      beep();
      try { env.navigator?.vibrate?.(200); } catch {}
      notify(match, gameName);
      startFlash();
    }

    return { update, stop: stopFlash };
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

  return { observe, secondsLeft, clockText, clock, notice, createFields, createAlerter, URGENT_SECONDS };
})();
