"use strict";
// Register early: installation does not depend on login or API availability.
(() => {
  const button = document.getElementById("install-portal");
  const help = document.getElementById("install-help");
  const message = document.getElementById("install-message");
  let prompt = null;
  let prompting = false;
  const installed = () => {
    prompt = null;
    button.hidden = true;
    help.hidden = true;
  };
  if (window.matchMedia("(display-mode: standalone)").matches || navigator.standalone)
    installed();
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    prompt = event;
    button.hidden = false;
  });
  window.addEventListener("appinstalled", installed);
  button.addEventListener("click", async () => {
    if (!prompt || prompting) return;
    const current = prompt;
    prompt = null;
    prompting = true;
    button.disabled = true;
    try {
      await current.prompt();
      const result = await current.userChoice;
      message.textContent = result.outcome === "accepted"
        ? "Installation requested. Open Playroom from your Home Screen or apps."
        : "You can install later from your browser’s menu.";
    } catch {
      message.textContent = "Use your browser’s menu to install Playroom.";
    } finally {
      prompting = false;
      button.disabled = false;
      button.hidden = !prompt;
    }
  });
})();
