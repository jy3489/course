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
    const spend = kind === "campaign"
      ? (() => {
          const input = el("input", {
            type: "number",
            min: 0,
            max: variant.budget_usd ?? 100,
            step: 0.01,
            value: variant.spend_usd,
            "aria-label": `Actual spend for ${variant.name}`,
          });
          const status = el("span", { class: "muted", role: "status" });
          const form = el(
            "form",
            { class: "spend-form" },
            input,
            el("button", { type: "submit", textContent: "Save" }),
            status,
          );
          form.addEventListener("submit", async (event) => {
            event.preventDefault();
            status.textContent = "Saving…";
            try {
              await apiRequest(
                "PATCH",
                `/admin/experiments/campaign-links/${encodeURIComponent(variant.variant_id)}`,
                { body: { spend_usd: Number(input.value) } },
              );
              await refresh();
            } catch (error) {
              status.textContent = `Couldn't save spend: ${error.message}`;
            }
          });
          return form;
        })()
      : "—";
    return el(
      "tr",
      {},
      el("td", { textContent: variant.name }),
      el("td", { class: "num", textContent: kind === "skin" ? variant.assigned : "—" }),
      el("td", { class: "num", textContent: kind === "campaign" ? variant.clicks : "—" }),
      el("td", { class: "num", textContent: variant.signups }),
      el("td", { class: "num", textContent: variant.match_starts }),
      el("td", { class: "num", textContent: variant.returning_active_users }),
      el("td", { class: "num", textContent: kind === "campaign" && variant.budget_usd != null ? `$${variant.budget_usd.toFixed(2)}` : "—" }),
      el("td", { class: "num", textContent: spend }),
      el("td", { class: "num", textContent: kind === "campaign" ? `${variant.signup_rate}%` : "—" }),
      el("td", { class: "num", textContent: `${variant.match_start_rate}%` }),
      el("td", { class: "num", textContent: `${variant.return_rate}%` }),
      el("td", { class: "num", textContent: kind === "campaign" && variant.cost_per_signup_usd !== null ? `$${variant.cost_per_signup_usd.toFixed(2)}` : "—" }),
      el("td", { class: "num", textContent: kind === "campaign" && variant.cost_per_match_user_usd !== null ? `$${variant.cost_per_match_user_usd.toFixed(2)}` : "—" }),
      el("td", { class: "num", textContent: kind === "campaign" && variant.cost_per_returning_user_usd !== null ? `$${variant.cost_per_returning_user_usd.toFixed(2)}` : "—" }),
      el("td", {}, link),
    );
  });
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
                ["Variant", "Assigned", "Clicks", "Signups", "Match starts", "7-day return", "Budget", "Actual spend", "Signup rate", "Start rate", "Return rate", "Cost/signup", "Cost/start", "Cost/return", "Link"].map(
                  (heading, index) => el("th", { class: index > 0 && index < 14 ? "num" : "", textContent: heading }),
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
  return el(
    "div",
    { class: "experiment-manager" },
    variantForm({
      title: "Create an advertisement campaign link",
      endpoint: "/admin/experiments/campaign-links",
      note: "Share this link to count visits and attributed activity.",
      withLink: true,
    }),
    variantForm({
      title: "Add a UI skin variant",
      endpoint: "/admin/experiments/skin-variants",
      note: "Add the matching CSS theme before the variant is assigned.",
    }),
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
    const [stats, experiments] = await Promise.all([
      statsResponse.json(),
      experimentsResponse.ok ? experimentsResponse.json() : Promise.resolve(null),
    ]);
    $("error").textContent = "";
    if (stats.schema_version === 2) {
      const rows=Object.entries(stats.counts).sort(([a],[b])=>a.localeCompare(b));
      $("content").replaceChildren(table("Database records",["Record type","Count"],rows));
      if (stats.snapshot) {
        const snapshot = stats.snapshot;
        const anchor = new Date(`${snapshot.anchor}T00:00:00Z`);
        const dates = Array.from({length:30}, (_, i) => {
          const date = new Date(anchor);
          date.setUTCDate(date.getUTCDate() - 29 + i);
          return date.toISOString().slice(0,10);
        });
        $("content").append(
          el("p", {textContent:`Historical report built ${formatTime(snapshot.generated_at)}`}),
          ...["users", "games", "matches"].map(kind => barChart(
            `New ${kind} per day`, dates.map(date => ({date, count:snapshot.days[date]?.[kind] ?? 0}))
          )),
        );
      }
      if (experiments) $("content").append(
        el("p", {textContent:`Experiment report built ${formatTime(experiments.generated_at)}`}),
        experimentTable("UI skin experiment", experiments.skin_experiment, "skin"),
        experimentTable("Advertisement campaigns", experiments.campaign_experiment, "campaign"),
        experimentManagement(),
      );
      else $("content").append(el("p", {textContent:"Experiment report is currently unavailable."}));
    } else {
      if (!experiments) throw new Error(`GET /admin/experiments: ${experimentsResponse.status}`);
      render(stats, experiments);
    }
    $("freshness").textContent = `Loaded ${formatTime(stats.generated_at)}`;
  } catch (error) {
    $("error").textContent = `Couldn't load statistics: ${error.message}`;
  }
}

$("refresh").addEventListener("click", refresh);
refresh();
