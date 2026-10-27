"use strict";

// Presentation only: never moves/reloads the iframe or changes a match's state.
window.PortalMatchLayout = (() => {
  const $ = id => document.getElementById(id);
  const landscape = window.matchMedia(
    "(orientation: landscape) and (max-width: 1024px), (orientation: landscape) and (max-width: 1366px) and (pointer: coarse)",
  );
  let match = null;
  let manualFocus = null;
  let controlsOpen = false;
  let online = navigator.onLine;
  let previousStatus = null;
  let previousConflict = null;

  function render() {
    const active = Boolean(match);
    const waiting = match?.status === "waiting_for_players";
    const focused = active && !waiting && (manualFocus ?? landscape.matches);
    const focusChanged = active && document.body.classList.contains("match-page") &&
      document.body.classList.contains("match-focus") !== focused;
    document.body.classList.toggle("match-page", active);
    document.body.classList.toggle("match-focus", focused);
    document.body.classList.toggle("match-lobby", active && waiting);
    // Fade the toolbar after the layout switches; never transform/reload the game iframe.
    if (focusChanged && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const toolbar = $("match-focus-toggle").parentElement;
      toolbar?.getAnimations?.().forEach(animation => animation.cancel());
      toolbar?.animate?.([{opacity:.35, transform:"translateY(-4px)"}, {opacity:1, transform:"translateY(0)"}],
        {duration:220, easing:"ease-out"});
    }
    $("match-tools").hidden = !active || (!waiting && !controlsOpen);
    $("match-controls-toggle").hidden = waiting;
    $("match-controls-toggle").setAttribute("aria-expanded", String(waiting || controlsOpen));
    $("match-controls-toggle").textContent = controlsOpen ? I18n.t("ui.portal.hide_controls", {}, "Hide controls") : I18n.t("ui.portal.controls", {}, "Controls");
    $("match-focus-toggle").hidden = waiting;
    $("match-focus-toggle").setAttribute("aria-pressed", String(focused));
    $("match-focus-toggle").textContent = focused ? I18n.t("ui.portal.exit_focus", {}, "Exit focus") : I18n.t("ui.portal.focus", {}, "Focus");
    $("match-toolbar-title").textContent = active ? $("match-title").textContent : "";
    const error = $("match-message").classList.contains("error") ? $("match-message").textContent : "";
    const notices = [];
    if (match?.conflict) notices.push("Sync needs review — open Controls");
    else if (error) notices.push(error);
    if (match?.pending) notices.push(`${match.pending} move${match.pending === 1 ? "" : "s"} saved on this device · waiting to sync`);
    else if (active && !online) notices.push(match.localMode ? "Offline · playing on this device" : "Offline · moves save on this device");
    if (match?.speedNotice) notices.push(match.speedNotice); // speed matches: time ran out
    if (match?.status === "over") notices.push($("match-result").textContent || "Match finished");
    $("match-play-notice").textContent = notices.join(" · ");
    $("match-play-notice").hidden = notices.length === 0;
    $("match-play-notice").classList.toggle("error", Boolean(error || match?.conflict));
  }

  function update(next) {
    if (next?.id !== match?.id) {
      manualFocus = null;
      controlsOpen = next?.status === "over";
      previousStatus = null;
      previousConflict = null;
    }
    // Reveal rematch/conflict actions once, not again on every poll.
    if (next?.status === "over" && previousStatus !== "over") controlsOpen = true;
    const conflict = next?.conflict?.message || null;
    if (conflict && conflict !== previousConflict) controlsOpen = true;
    match = next;
    previousStatus = next?.status;
    previousConflict = conflict;
    render();
  }

  function init(onExit) {
    $("match-back").addEventListener("click", onExit);
    $("match-controls-toggle").addEventListener("click", () => {
      controlsOpen = !controlsOpen;
      render();
    });
    $("match-focus-toggle").addEventListener("click", () => {
      manualFocus = !(manualFocus ?? landscape.matches);
      render();
    });
    document.addEventListener("i18n-changed", render);
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape" || !match) return;
      if (controlsOpen && match.status !== "waiting_for_players") {
        controlsOpen = false;
        render();
        $("match-controls-toggle").focus();
      } else if (document.body.classList.contains("match-focus")) {
        manualFocus = false;
        render();
        $("match-focus-toggle").focus();
      }
    });
    // A new orientation gets its appropriate default; ordinary resizing and
    // polling preserve a person's explicit Focus/Controls choice.
    landscape.addEventListener("change", () => { manualFocus = null; render(); });
    new MutationObserver(render).observe($("match-message"), {childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["class"]});
    render();
  }

  return { init, update, connection(value) { online = value; render(); } };
})();
