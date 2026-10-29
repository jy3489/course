"use strict";

(() => {
  const ACCOUNTS_KEY = "game-platform.accounts.v1";
  const CURRENT_KEY = "game-platform.current-account.v1";
  const SKIN_CACHE_PREFIX = "game-platform.experiment-skin.v1:";
  const SKIN_KEY_PATTERN = /^[a-z0-9][a-z0-9-]{0,49}$/;

  function currentAccount() {
    try {
      const currentId = localStorage.getItem(CURRENT_KEY);
      const accounts = JSON.parse(localStorage.getItem(ACCOUNTS_KEY) || "[]");
      return accounts.find((account) => account.id === currentId) ?? null;
    } catch (error) {
      console.warn("Skin assignment is unavailable in this browser.", error);
      return null;
    }
  }

  function applySkin(key) {
    if (key && SKIN_KEY_PATTERN.test(key))
      document.documentElement.dataset.skin = key;
    else delete document.documentElement.dataset.skin;
  }

  async function applyCurrentSkin() {
    const account = currentAccount();
    if (!account) {
      applySkin(null);
      return;
    }
    const cacheKey = `${SKIN_CACHE_PREFIX}${account.id}`;
    try {
      applySkin(localStorage.getItem(cacheKey));
    } catch (error) {
      console.warn("Cached skin assignment could not be read.", error);
    }

    try {
      const response = await fetch("/users/me/experiments/skin", {
        headers: {
          "X-User-Id": account.id,
          "X-User-Password": account.password,
        },
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error(`GET skin assignment: ${response.status}`);
      const assignment = await response.json();
      const key = assignment?.key;
      applySkin(key);
      if (key && SKIN_KEY_PATTERN.test(key))
        localStorage.setItem(cacheKey, key);
    } catch (error) {
      console.error("Could not refresh the UI skin assignment.", error);
    }
  }

  window.ExperimentCampaignCode = () => {
    try {
      const code = localStorage.getItem("game-platform.experiment-campaign.v1");
      return code && /^[a-z0-9][a-z0-9-]{0,49}$/.test(code) ? code : null;
    } catch (error) {
      console.warn("Campaign attribution is unavailable in this browser.", error);
      return null;
    }
  };

  applyCurrentSkin();
  document.addEventListener("auth-changed", applyCurrentSkin);
})();
