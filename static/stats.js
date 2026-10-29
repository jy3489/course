// Statistics page: numbers about users, games and matches from /admin/stats.

"use strict";

const $ = (id) => document.getElementById(id);

function card(value, label) {
  return el("div", { class: "card" }, el("div", { class: "value", textContent: value }), el("div", { class: "label", textContent: label }));
}

function cards(items) {
  return el("div", { class: "cards" }, items.map(([value, label]) => card(value, label)));
}

/** Bars for [{date, count}], oldest first. */
function barChart(title, series) {
  const max = Math.max(1, ...series.map((point) => point.count));
  const bars = series.map((point) =>
    el("div", {
      class: "bar",
      title: `${point.date}: ${point.count}`,
      style: `height: ${Math.round((point.count / max) * 100)}%`,
    }),
  );
  const total = series.reduce((sum, point) => sum + point.count, 0);
  return el(
    "div",
    { class: "panel" },
    el("h3", { textContent: `${title} (${total} in the last ${series.length} days)` }),
    el("div", { class: "bars" }, bars),
    el("div", { class: "axis" }, el("span", { textContent: series[0]?.date.slice(5) }), el("span", { textContent: series.at(-1)?.date.slice(5) })),
  );
}

function table(title, headers, rows) {
  return el(
    "div",
    { class: "panel" },
    el("h3", { textContent: title }),
    rows.length
      ? el(
          "table",
          {},
          el("thead", {}, el("tr", {}, headers.map((h, i) => el("th", { class: i ? "num" : "", textContent: h })))),
          el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell, i) => el("td", { class: i ? "num" : "", textContent: cell }))))),
        )
      : el("div", { class: "muted", textContent: "None yet." }),
  );
}

function experimentTable(title, experiment, kind) {
  const rows = experiment.variants.map((variant) => {
    const link = variant.link
      ? el("a", {
          href: variant.link,
          textContent: "Open",
          target: "_blank",
          rel: "noopener",
        })
      : "—";
    const status = el("span", { class: "muted delete-status", role: "status" });
    const remove = el("button", {
      type: "button",
      class: "danger",
      textContent: "Delete",
      "aria-label": `Delete ${variant.name}`,
      onclick: async (event) => {
        if (
          !window.confirm(
            `Delete "${variant.name}"? Its assignments and recorded experiment metrics will also be deleted.`,
          )
        )
          return;
        const button = event.currentTarget;
        button.disabled = true;
        status.textContent = "Deleting…";
        try {
          await apiRequest(
            "DELETE",
            `/admin/experiments/${encodeURIComponent(experiment.id)}/variants/${encodeURIComponent(variant.variant_id)}`,
          );
          await refresh();
        } catch (error) {
          button.disabled = false;
          status.textContent = `Couldn't delete variant: ${error.message}`;
        }
      },
    });
    return el(
      "tr",
      {},
      el("td", { textContent: variant.name }),
      ...(kind === "skin"
        ? [
            el("td", { class: "num", textContent: variant.assigned }),
            el("td", { class: "num", textContent: variant.signups }),
            el("td", { class: "num", textContent: variant.match_starts }),
            el("td", { class: "num", textContent: variant.returning_active_users }),
            el("td", { class: "num", textContent: `${variant.match_start_rate}%` }),
            el("td", { class: "num", textContent: `${variant.return_rate}%` }),
          ]
        : [
            el("td", { class: "num", textContent: variant.clicks }),
            el("td", { class: "num", textContent: variant.signups }),
            el("td", { class: "num", textContent: variant.match_starts }),
            el("td", { class: "num", textContent: variant.returning_active_users }),
            el("td", {
              class: "num",
              textContent:
                variant.budget_usd == null
                  ? "—"
                  : `$${variant.budget_usd.toFixed(2)}`,
            }),
            el("td", { class: "num", textContent: `${variant.signup_rate}%` }),
            el("td", { class: "num", textContent: `${variant.match_start_rate}%` }),
            el("td", { class: "num", textContent: `${variant.return_rate}%` }),
            el("td", {}, link),
          ]),
      el("td", { class: "variant-actions" }, remove, status),
    );
  });
  const headings =
    kind === "skin"
      ? ["Variant", "Assigned", "Signups", "Match starts", "7-day return", "Start rate", "Return rate", "Actions"]
      : ["Campaign", "Clicks", "Signups", "Match starts", "7-day return", "Budget", "Signup rate", "Start rate", "Return rate", "Link", "Actions"];
  return el(
    "div",
    { class: "panel experiment-panel" },
    el("h3", { textContent: `${title}${experiment.active ? "" : " (paused)"}` }),
    rows.length
      ? el(
          "div",
          { class: "table-scroll" },
          el(
            "table",
            {},
            el(
              "thead",
              {},
              el(
                "tr",
                {},
                headings.map((heading, index) =>
                  el("th", {
                    class:
                      index > 0 && index < headings.length - 1 && heading !== "Link"
                        ? "num"
                        : "",
                    textContent: heading,
                  }),
                ),
              ),
            ),
            el("tbody", {}, rows),
          ),
        )
      : el("div", { class: "muted", textContent: "No variants yet." }),
  );
}

function variantForm({ title, endpoint, note, withLink = false }) {
  const name = el("input", { required: true, maxlength: 100, placeholder: "Student campaign A" });
  const key = el("input", { required: true, maxlength: 50, pattern: "[a-z0-9][a-z0-9-]*", placeholder: "student-campaign-a" });
  const budget = withLink
    ? el("input", { type: "number", min: 0, max: 100, step: 0.01, value: 100, required: true })
    : null;
  const result = el("div", { class: "muted", role: "status" });
  name.addEventListener("input", () => {
    if (!key.dataset.edited) {
      key.value = name.value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50);
    }
  });
  key.addEventListener("input", () => {
    key.dataset.edited = "true";
  });
  const form = el(
    "form",
    { class: "experiment-form" },
    el("strong", { textContent: title }),
    el("label", {}, "Name ", name),
    el("label", {}, "Link key ", key),
    ...(budget ? [el("label", {}, "Budget (USD, max $100) ", budget)] : []),
    el("button", { type: "submit", textContent: withLink ? "Create campaign link" : "Add skin variant" }),
    el("span", { class: "muted", textContent: note }),
    result,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    result.textContent = "Creating…";
    try {
      const created = await apiRequest("POST", endpoint, {
        body: {
          name: name.value.trim(),
          key: key.value.trim(),
          ...(budget ? { budget_usd: Number(budget.value) } : {}),
        },
      });
      result.replaceChildren(
        withLink
          ? el("a", { href: created.url, textContent: created.url, target: "_blank", rel: "noopener" })
          : document.createTextNode(`Added ${created.name} (${created.key}). Deploy a matching CSS rule before assigning users to it.`),
      );
      key.value = "";
      name.value = "";
      key.dataset.edited = "";
      await refresh();
    } catch (error) {
      result.textContent = `Couldn't create variant: ${error.message}`;
    }
  });
  return form;
}

function experimentManagement() {
  const steps = (title, items) =>
    el(
      "details",
      { class: "experiment-help" },
      el("summary", { textContent: title }),
      el("ol", {}, items.map((item) => el("li", { textContent: item }))),
    );
  return el(
    "div",
    { class: "experiment-manager" },
    el(
      "section",
      { class: "experiment-manager-item" },
      variantForm({
        title: "Create an advertisement campaign link",
        endpoint: "/admin/experiments/campaign-links",
        note: "Share this link to count visits and attributed activity.",
        withLink: true,
      }),
      steps("Step-by-step: create and use a campaign link", [
        "Enter a name that will help you recognize this campaign in the results.",
        "Choose a unique link key. The form suggests one from the name; use lowercase letters, numbers, and hyphens only.",
        "Click Create campaign link. The new link appears in the campaign results above.",
        "Copy the generated link using Open (or right-click it and choose Copy link). Share this exact link in the campaign you want to measure. Create a separate link for each campaign.",
        "Return to this page to compare clicks, attributed signups, match starts, and 7-day returns. A signup is attributed when someone creates an account after visiting the link.",
      ]),
    ),
    el(
      "section",
      { class: "experiment-manager-item" },
      variantForm({
        title: "Add a UI skin variant",
        endpoint: "/admin/experiments/skin-variants",
        note: "The matching skin design must be deployed before users are assigned to it.",
      }),
      steps("Step-by-step: prepare and add a UI skin", [
        "Ask the developer or student creating the skin to provide its display name and exact key. The key uses lowercase letters, numbers, and hyphens.",
        "Have them add and deploy the visual design for that exact key before adding it here. Adding a variant only registers it for assignment; it does not create, upload, or deploy the design.",
        "Enter the agreed display name and key, then click Add skin variant. Check that the variant appears in the results above.",
        "New assignments can use the new skin after it is added. Existing users keep their current assignment.",
        "If a skin is retired, use its Delete button above. Deleting it also clears its assignments and recorded results; users assigned to it will receive another available skin next time.",
      ]),
    ),
  );
}

function gamesPanel(games) {
  const detail = el("div", { class: "chat-detail" });
  const rows = games.map((g) =>
    el(
      "tr",
      {},
      el("td", { textContent: g.deleted ? `${g.name} (deleted)` : g.name }),
      el("td", { textContent: g.owner }),
      el("td", { class: "num", textContent: g.versions }),
      el("td", { class: "num", textContent: g.matches }),
      el(
        "td",
        {},
        el("button", {
          type: "button",
          textContent: "View",
          onclick: () => showAgentConversation(g, detail),
        }),
      ),
    ),
  );
  return el(
    "div",
    { class: "panel" },
    el("h3", { textContent: "Matches by game" }),
    games.length
      ? el(
          "table",
          {},
          el(
            "thead",
            {},
            el(
              "tr",
              {},
              ["Game", "Owner", "Versions", "Matches", "Agent conversation"].map((h, i) =>
                el("th", { class: i > 0 && i < 4 ? "num" : "", textContent: h }),
              ),
            ),
          ),
          el("tbody", {}, rows),
        )
      : el("div", { class: "muted", textContent: "None yet." }),
    detail,
  );
}

async function showAgentConversation(game, detail) {
  detail.replaceChildren(
    el("div", { class: "muted", textContent: `Loading the agent conversation for ${game.name}…` }),
  );
  try {
    const response = await fetch(
      `/admin/games/${encodeURIComponent(game.game_id)}/agent-conversation`,
    );
    if (response.status === 404) {
      detail.replaceChildren(
        el("div", { class: "muted", textContent: `No agent conversation recorded for ${game.name}.` }),
      );
      return;
    }
    if (!response.ok) throw new Error(`GET agent-conversation: ${response.status}`);
    const conversation = await response.json();
    detail.replaceChildren(
      el("h4", {
        textContent: `Agent conversation — ${game.name} (${conversation.entries.length} messages)`,
      }),
      ...conversation.entries.map((entry) =>
        el(
          "div",
          { class: "chat-entry" },
          el("div", {
            class: "chat-meta",
            textContent: `${entry.role === "user" ? "User" : "Agent"} · ${formatTime(entry.created_at)} · ${
              entry.game_version ? `game version ${entry.game_version}` : "not yet published"
            }`,
          }),
          el("div", { class: "chat-text", textContent: entry.text }),
        ),
      ),
    );
  } catch (error) {
    detail.replaceChildren(
      el("div", { class: "muted", textContent: `Couldn't load the conversation: ${error.message}` }),
    );
  }
}

function render(stats, experiments) {
  const { users, games, matches } = stats;
  $("content").replaceChildren(
    el("h2", { textContent: "Portal: users and matches" }),
    cards([
      [users.total, "users"],
      [users.who_played_a_match, "users who played a match"],
      [users.guests_only, "guest-only users"],
      [users.with_google, "linked to Google"],
      [users.with_email, "linked to email"],
      [matches.total, "matches"],
      [matches.by_status.waiting_for_players, "waiting for players"],
      [matches.by_status.ongoing, "ongoing"],
      [matches.by_status.over, "over"],
      [matches.total_moves, "moves made"],
      [matches.avg_moves_per_match, "moves per match"],
      [matches.ended_because_a_player_left, "ended by a player leaving"],
    ]),
    el(
      "div",
      { class: "panels" },
      barChart("New users per day", users.new_per_day),
      barChart("New matches per day", matches.new_per_day),
      table("Users by language", ["Language", "Users"], Object.entries(users.by_locale ?? {})),
    ),
    el("h2", { textContent: "Builder: games" }),
    cards([
      [games.live, "live games"],
      [games.deleted, "deleted games"],
      [games.versions, "versions saved"],
      [games.builders, "game builders"],
    ]),
    el(
      "div",
      { class: "panels" },
      barChart("New games per day", games.new_per_day),
      gamesPanel(games.per_game),
      table("Top builders", ["User", "Live games"], games.top_builders.map((b) => [b.user, b.games])),
    ),
    el("h2", { textContent: "A/B experiments" }),
    el(
      "p",
      { class: "muted", textContent: "Results are shown separately. Returning active users are signups that started a match during days 7–14 after signup." },
    ),
    el(
      "div",
      { class: "panels experiment-results" },
      experimentTable("UI skin experiment", experiments.skin_experiment, "skin"),
      experimentTable("Advertisement campaigns", experiments.campaign_experiment, "campaign"),
    ),
    experimentManagement(),
  );
}

async function refresh() {
  try {
    const [statsResponse, experimentsResponse] = await Promise.all([
      fetch("/admin/stats"),
      fetch("/admin/experiments"),
    ]);
    if (!statsResponse.ok) throw new Error(`GET /admin/stats: ${statsResponse.status}`);
    if (!experimentsResponse.ok) throw new Error(`GET /admin/experiments: ${experimentsResponse.status}`);
    const [stats, experiments] = await Promise.all([
      statsResponse.json(),
      experimentsResponse.json(),
    ]);
    $("error").textContent = "";
    render(stats, experiments);
    $("freshness").textContent = `Loaded ${formatTime(stats.generated_at)}`;
  } catch (error) {
    $("error").textContent = `Couldn't load statistics: ${error.message}`;
  }
}

$("refresh").addEventListener("click", refresh);
refresh();
