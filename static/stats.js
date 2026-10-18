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

function render(stats) {
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
      table("Matches by game", ["Game", "Owner", "Versions", "Matches"], games.per_game.map((g) => [g.deleted ? `${g.name} (deleted)` : g.name, g.owner, g.versions, g.matches])),
      table("Top builders", ["User", "Live games"], games.top_builders.map((b) => [b.user, b.games])),
    ),
  );
}

async function refresh() {
  try {
    const response = await fetch("/admin/stats");
    if (!response.ok) throw new Error(`GET /admin/stats: ${response.status}`);
    const stats = await response.json();
    $("error").textContent = "";
    render(stats);
    $("freshness").textContent = `Loaded ${formatTime(stats.generated_at)}`;
  } catch (error) {
    $("error").textContent = `Couldn't load statistics: ${error.message}`;
  }
}

$("refresh").addEventListener("click", refresh);
refresh();
