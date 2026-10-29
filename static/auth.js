// Accounts and login, shared by the portal and the builder.
//
// The browser remembers every logged-in account in localStorage:
// [{id, display_name, password, linked: [{kind, identifier, linked_at}]}].
// The password is the API credential (X-User-Password); it never leaves the browser
// except in API request headers.

"use strict";

// Pages that don't load i18n.js get English: the fallback text passed to I18n.t.
if (typeof I18n === "undefined") {
  globalThis.I18n = {
    t: (key, params = {}, fallback = key) =>
      fallback.replace(/\{(\w+)\}/g, (match, name) => (name in params ? params[name] : match)),
  };
}

const Auth = (() => {
  const ACCOUNTS_KEY = "game-platform.accounts.v1";
  const CURRENT_KEY = "game-platform.current-account.v1";

  function load() {
    try {
      return JSON.parse(localStorage.getItem(ACCOUNTS_KEY)) ?? [];
    } catch {
      return [];
    }
  }

  function save(accounts) {
    localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(accounts));
  }

  function changed() {
    document.dispatchEvent(new CustomEvent("auth-changed"));
  }

  /** All remembered accounts, oldest first. */
  function accounts() {
    return load();
  }

  /** The current account, or null when logged out. */
  function current() {
    const id = localStorage.getItem(CURRENT_KEY);
    return load().find((account) => account.id === id) ?? null;
  }

  function setCurrent(id) {
    if (id) localStorage.setItem(CURRENT_KEY, id);
    else localStorage.removeItem(CURRENT_KEY);
    changed();
  }

  /** Stores an /auth/* response (or a POST /users response) and makes it current.
   * Drops the merged-away account from the local list, if the login merged one:
   * it still exists server-side, marked as replaced by the surviving account,
   * so keeping a separate local entry would just duplicate the survivor. */
  function upsert(response) {
    const rest = load().filter(
      (account) =>
        account.id !== response.user_id && account.id !== response.merged_from_user_id
    );
    rest.push({
      id: response.user_id,
      display_name: response.display_name,
      picture_url: response.picture_url ?? "",
      password: response.password,
      linked: response.linked_accounts ?? [],
      referral_code: response.referral_code ?? null,
    });
    save(rest);
    // This browser has an account now, so a remembered invite has done its job.
    forgetInviter();
    setCurrent(response.user_id);
    return response;
  }

  /** Saves [code] as account [id]'s referral code (accounts saved before codes existed
   * learn theirs from the server later). */
  function setPicture(id, picture) {
    const all = load(), account = all.find(a => a.id === id);
    if (!account || !picture || account.picture_url === picture) return false;
    account.picture_url = picture;
    save(all);
    return true;
  }

  function setReferralCode(id, code) {
    const accounts = load();
    const account = accounts.find((a) => a.id === id);
    if (!account || !code || account.referral_code === code) return;
    account.referral_code = code;
    save(accounts);
  }

  /** Forgets an account (log out). Falls back to another remembered account. */
  function remove(id) {
    const rest = load().filter((account) => account.id !== id);
    save(rest);
    setCurrent(rest[0]?.id ?? null);
  }

  function removeAll() {
    save([]);
    setCurrent(null);
  }

  /** Headers for apiRequest's {user}: the account itself works. */
  function headers(account) {
    return account
      ? { "X-User-Id": account.id, "X-User-Password": account.password }
      : {};
  }

  // Invite links
  //
  // An invite link names its sender: the short form is /r/<referral code>, or
  // /r/<code>#match=<match id> for a match; the server redirects it to
  // /portal?ref=<code> (keeping the #match part). Older links carry the sender's user
  // id instead: /portal#ref=<id> or /portal#match=<match id>&ref=<id>. The first ref this browser sees is
  // remembered until an account is created or logged in here, so it still counts if
  // the person looks around first and signs up later. The server records it as the
  // new account's inviter (and ignores it for accounts that already exist).

  const INVITER_KEY = "game-platform.invited-by.v1";

  function refInUrl() {
    for (const part of [location.hash.slice(1), location.search.slice(1)]) {
      const ref = new URLSearchParams(part).get("ref");
      if (ref) return ref;
    }
    return null;
  }

  /** Remembers the inviter named in the current URL, unless one is remembered already
   * or the link is one of this browser's own accounts. */
  function rememberInviter() {
    try {
      const ref = refInUrl();
      if (!ref || localStorage.getItem(INVITER_KEY)) return;
      if (load().some((account) => account.id === ref || account.referral_code === ref)) return;
      localStorage.setItem(INVITER_KEY, ref);
    } catch {
      // Storage can be unavailable (private mode); the invite just isn't counted.
    }
  }

  function forgetInviter() {
    try {
      localStorage.removeItem(INVITER_KEY);
    } catch {}
  }

  /** [body] for a request that may create an account, plus the remembered inviter. */
  function newAccountBody(body) {
    let ref = null;
    try {
      ref = localStorage.getItem(INVITER_KEY);
    } catch {}
    return ref ? { ...body, invited_by_user_id: ref } : body;
  }

  /** A link that credits [account] with everyone who signs up through it: the short
   * /r/<code> form, or the long one for an account that has no code yet.
   * [hash] adds more of the portal's hash parameters, e.g. "match=<id>". */
  function inviteLink(account, hash = "") {
    if (account.referral_code) {
      return `${location.origin}/r/${encodeURIComponent(account.referral_code)}${hash ? `#${hash}` : ""}`;
    }
    const params = new URLSearchParams(hash);
    params.set("ref", account.id);
    return `${location.origin}/portal#${params}`;
  }

  rememberInviter();
  window.addEventListener?.("hashchange", rememberInviter);

  // Login dialog

  let configPromise = null;
  function authConfig() {
    configPromise ??= apiRequest("GET", "/auth/config").catch(() => ({
      google_client_id: "",
      phone_login_enabled: false,
      phone_call_enabled: false,
      email_login_enabled: false,
    }));
    return configPromise;
  }

  // The dialog being filled in, for the Google callback.
  let pendingDialog = null;

  let googleLoading = null;
  function ensureGoogle(clientId) {
    googleLoading ??= new Promise((resolve, reject) => {
      if (window.google?.accounts?.id) return resolve();
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error("couldn't load Google's login script"));
      document.head.appendChild(script);
    });
    return googleLoading.then(() => {
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: (response) => pendingDialog?.onGoogle(response.credential),
        auto_select: false,
      });
    });
  }


  function toast(text) {
    let box = document.getElementById("auth-toast");
    if (!box) {
      box = el("div", { id: "auth-toast", class: "auth-toast" });
      document.body.appendChild(box);
    }
    box.textContent = text;
    box.hidden = false;
    clearTimeout(box._timer);
    box._timer = setTimeout(() => (box.hidden = true), 6000);
  }

  function methodLabel(kind, identifier) {
    switch (kind) {
      case "google":
        return "Google";
      case "phone":
        return `text ${identifier}`;
      case "email":
        return identifier;
      default:
        return identifier;
    }
  }

  /** Opens the login dialog. In "link" mode the credential is linked to the current
   * account instead of logging in (merging whichever account already holds it). */
  function openDialog(mode = "login") {
    closeDialog();
    const account = current();
    if (mode === "link" && !account) mode = "login";
    const dialog = {
      mode,
      account,
      root: el("div", { class: "auth-dialog-backdrop" }),
      message: el("div", { class: "message" }),
    };
    pendingDialog = dialog;

    const box = el("div", { class: "auth-dialog", role: "dialog", "aria-modal": "true" });
    const title = el("h2", {
      textContent: mode === "link" ? I18n.t("ui.auth.link_title", { name: account.display_name }, `Link a login to ${account.display_name}`) : I18n.t("ui.auth.log_in", {}, "Log in"),
    });
    const close = el("button", { type: "button", class: "auth-close", textContent: "×", "aria-label": I18n.t("ui.action.close", {}, "Close") });
    close.onclick = closeDialog;
    dialog.root.onclick = (event) => {
      if (event.target === dialog.root) closeDialog();
    };
    box.append(close, title, dialog.message);
    if (mode === "link") {
      box.append(
        el("p", { class: "muted", textContent: I18n.t("ui.auth.merge_note", {}, "If this login belongs to another of your accounts, that account is merged into this one.") })
      );
    }
    const methods = el("div", { class: "auth-methods" });
    box.append(methods);
    dialog.root.append(box);
    document.body.append(dialog.root);

    dialog.onGoogle = async (idToken) => {
      try {
        const response = await apiRequest("POST", "/auth/google", {
          user: mode === "link" ? account : null,
          body: mode === "link" ? { id_token: idToken } : newAccountBody({ id_token: idToken }),
        });
        finishLogin(dialog, response);
      } catch (error) {
        dialogFailed(dialog, error);
      }
    };


    authConfig().then((config) => {
      if (pendingDialog !== dialog) return; // closed while loading
      if (config.google_client_id) {
        const holder = el("div", { class: "auth-method" });
        methods.append(holder);
        ensureGoogle(config.google_client_id)
          .then(() =>
            window.google.accounts.id.renderButton(holder, {
              theme: "outline",
              size: "large",
              width: 280,
            })
          )
          .catch((error) => dialogFailed(dialog, error));
      }
      if (config.phone_login_enabled) {
        methods.append(codeMethod(dialog, "phone", I18n.t("ui.auth.phone", {}, "Phone number"), "+15551234567", "/auth/phone", "phone_number", I18n.t("ui.auth.text_me_code", {}, "Text me a code"), config.phone_call_enabled));
      }
      if (config.email_login_enabled) {
        methods.append(codeMethod(dialog, "email", I18n.t("ui.auth.email", {}, "Email"), "player@example.com", "/auth/email", "email", I18n.t("ui.auth.email_me_code", {}, "Email me a code")));
      }
      methods.append(guestMethod(dialog));
      if (!methods.children.length) {
        dialog.message.textContent = I18n.t("ui.auth.no_methods", {}, "No login methods are configured on this server yet.");
        dialog.message.className = "message error";
      }
    });
  }

  function closeDialog() {
    pendingDialog?.root.remove();
    if (pendingDialog) pendingDialog = null;
  }

  function dialogFailed(dialog, error) {
    dialog.message.textContent =
      error instanceof ApiError ? error.message : String(error?.message ?? error);
    dialog.message.className = "message error";
  }

  function finishLogin(dialog, response) {
    upsert(response);
    closeDialog();
    if (response.merged_from_user_id) {
      toast(I18n.t("ui.auth.merged_toast", { name: response.display_name }, `Merged your other account into ${response.display_name}.`));
    }
  }

  /** A phone/email code form. Posts to [base]/start then [base]/verify. */
  function codeMethod(dialog, kind, label, placeholder, base, field, sendLabel, canCall = false) {
    const input = el("input", { type: kind === "phone" ? "tel" : "email", placeholder, "aria-label": label });
    const codeInput = el("input", { inputmode: "numeric", placeholder: "123456", "aria-label": I18n.t("ui.auth.code", {}, "Code"), hidden: true, style: "width: 120px" });
    const message = el("div", { class: "message" });
    let verificationId = null;

    const send = async (channel = "sms") => {
      message.textContent = "";
      message.className = "message";
      try {
        const started = await apiRequest("POST", `${base}/start`, {
          body: {
            [field]: input.value,
            ...(kind === "phone" ? { delivery_method: channel } : {}),
          },
        });
        verificationId = started.verification_id;
        codeInput.hidden = false;
        verifyButton.hidden = false;
        sendButton.textContent = I18n.t("ui.auth.send_again", {}, "Send again");
        message.textContent = I18n.t("ui.auth.code_sent", { minutes: Math.round(started.expires_in_seconds / 60) }, `Code sent — it expires in ${Math.round(started.expires_in_seconds / 60)} minutes.`);
        message.className = "message ok";
        codeInput.focus();
      } catch (error) {
        dialogFailed({ message }, error);
      }
    };
    const sendButton = el("button", {
      type: "button",
      textContent: sendLabel,
      onclick: () => send("sms"),
    });
    const callButton = kind === "phone" && canCall
      ? el("button", {
          type: "button",
          textContent: "Call me with a code",
          onclick: () => send("call"),
        })
      : null;
    const verifyButton = el("button", { type: "button", class: "primary", textContent: I18n.t("ui.auth.verify", {}, "Verify"), hidden: true });
    verifyButton.onclick = async () => {
      message.textContent = "";
      message.className = "message";
      try {
        const body = { verification_id: verificationId, code: codeInput.value };
        const response = await apiRequest("POST", `${base}/verify`, {
          user: dialog.mode === "link" ? dialog.account : null,
          body: dialog.mode === "link" ? body : newAccountBody(body),
        });
        finishLogin(dialog, response);
      } catch (error) {
        dialogFailed({ message }, error);
      }
    };
    return el("div", { class: "auth-method" },
      el("label", { class: "inline" }, `${label} `, input),
      el("div", { class: "inline-row" },
        sendButton,
        ...(callButton ? [callButton] : []),
        codeInput,
        verifyButton
      ),
      message
    );
  }

  function guestMethod(dialog) {
    const input = el("input", { placeholder: I18n.t("ui.auth.guest_name", {}, "Guest name"), "aria-label": I18n.t("ui.auth.guest_name", {}, "Guest name") });
    const button = el("button", { type: "button", textContent: I18n.t("ui.auth.continue_as_guest", {}, "Continue as guest") });
    button.onclick = async () => {
      const name = input.value.trim() || I18n.t("ui.auth.guest_default_name", { number: accounts().length + 1 }, `Guest ${accounts().length + 1}`);
      try {
        const user = await apiRequest("POST", "/users", {
          body: newAccountBody({ display_name: name }),
        });
        finishLogin(dialog, {
          user_id: user.id,
          display_name: user.display_name,
          password: user.password,
          referral_code: user.referral_code,
          linked_accounts: [],
          merged_from_user_id: null,
        });
      } catch (error) {
        dialogFailed(dialog, error);
      }
    };
    return el("div", { class: "auth-method" },
      el("label", { class: "inline" }, `${I18n.t("ui.auth.guest", {}, "Guest")} `, input),
      button
    );
  }

  /** An <img> for a stored picture URL, or null when there is none. */
  function avatarImg(url) {
    return url ? el("img", { class: "auth-avatar", src: url, alt: "", referrerPolicy: "no-referrer" }) : null;
  }

  // Account menu for the top bar

  /** Renders the account menu into [container]; call again after auth changes. */
  function renderMenu(container) {
    const account = current();
    const all = accounts();
    const panelWasOpen = container.querySelector(".auth-menu-panel")?.hidden === false;
    container.replaceChildren();
    const button = el("button", {
      type: "button",
      class: "auth-menu-button",
      "aria-haspopup": "true",
    },
      avatarImg(account?.picture_url),
      account ? account.display_name : I18n.t("ui.auth.log_in", {}, "Log in")
    );
    const panel = el("div", { class: "auth-menu-panel", hidden: true });
    panel.hidden = !panelWasOpen;
    panel.inert = !panelWasOpen;
    panel.setAttribute("aria-hidden", String(!panelWasOpen));
    const hidePanel = () => {
      panel.inert = true;
      panel.setAttribute("aria-hidden", "true");
      panel.hidden = true;
    };
    button.onclick = (event) => {
      event.stopPropagation();
      panel.hidden = !panel.hidden;
      panel.inert = panel.hidden;
      panel.setAttribute("aria-hidden", String(panel.hidden));
    };
    document.addEventListener("click", () => hidePanel(), { once: true });

    if (account) {
      panel.append(el("div", { class: "auth-menu-heading" },
        avatarImg(account.picture_url),
        `Logged in as ${account.display_name}`));
      if (account.linked.length) {
        panel.append(
          el("div", { class: "auth-menu-section" },
            el("div", { class: "muted", textContent: I18n.t("ui.auth.linked_logins", {}, "Linked logins") }),
            ...account.linked.map((link) =>
              el("div", { class: "auth-linked", textContent: methodLabel(link.kind, link.identifier) })
            )
          )
        );
      }
      panel.append(
        el("button", { type: "button", textContent: I18n.t("ui.auth.link_method", {}, "Link a login method"), onclick: () => { hidePanel(); openDialog("link"); } }),
        el("button", {
          type: "button",
          textContent: I18n.t("ui.auth.copy_invite", {}, "Copy my invite link"),
          title: "Everyone who signs up through this link counts as invited by you",
          onclick: () => {
            hidePanel();
            navigator.clipboard
              // Read again: the code may have arrived after the menu was drawn.
              .writeText(inviteLink(current() ?? account))
              .then(() => toast(I18n.t("ui.auth.invite_copied", {}, "Copied your invite link")));
          },
        })
      );
    }
    if (all.length > 1 || (all.length === 1 && !account)) {
      panel.append(el("div", { class: "auth-menu-heading", textContent: I18n.t("ui.auth.switch_account", {}, "Switch account") }));
      for (const other of all) {
        if (account && other.id === account.id) continue;
        panel.append(
          el("button", {
            type: "button",
            textContent: other.display_name,
            onclick: () => setCurrent(other.id),
          })
        );
      }
    }
    panel.append(
      el("button", { type: "button", textContent: account ? I18n.t("ui.auth.add_account", {}, "Add another account") : I18n.t("ui.auth.log_in", {}, "Log in"), onclick: () => { hidePanel(); openDialog("login"); } })
    );
    if (account) {
      panel.append(
        el("button", { type: "button", textContent: I18n.t("ui.auth.log_out_name", { name: account.display_name }, `Log out ${account.display_name}`), onclick: () => remove(account.id) }),
        el("button", { type: "button", textContent: I18n.t("ui.auth.log_out_all", {}, "Log out of all accounts"), onclick: removeAll })
      );
    }
    container.append(button, panel);
    return container;
  }

  return {
    accounts,
    current,
    setCurrent,
    upsert,
    remove,
    removeAll,
    headers,
    inviteLink,
    setReferralCode,
    setPicture,
    newAccountBody,
    openDialog,
    closeDialog,
    renderMenu,
    toast,
  };
})();
