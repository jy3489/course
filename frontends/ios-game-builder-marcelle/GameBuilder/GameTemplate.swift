/// The code a new game starts with: a tiny working game ("Race to 10") that follows the
/// course's state_changed / make_move protocol, so it can be test-played right away.
enum GameTemplate {
    static let starter = """
    <!doctype html>
    <html>
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body { margin: 0; padding: 16px; font-family: system-ui, sans-serif; background: #10141F; color: #EEF0F5; }
      button { font: inherit; font-size: 20px; padding: 12px 24px; border: 0; border-radius: 4px; background: #C6F432; color: #10141F; }
      button:disabled { opacity: 0.4; }
    </style>
    </head>
    <body>
    <h1>Race to 10</h1>
    <p id="status">Waiting for the platform…</p>
    <p id="scores"></p>
    <button id="add">+1</button>
    <script>
    "use strict";
    let latest = null;   // the last state_changed message
    let sent = false;    // at most one make_move per state_changed
    const $ = (id) => document.getElementById(id);
    const stateOf = (m) => m.state ?? { scores: m.players.map(() => 0), winner: null };

    function move(seat) {
      if (sent) return;
      sent = true;
      const scores = [...stateOf(latest).scores];
      scores[seat] = (scores[seat] ?? 0) + 1;
      const won = scores[seat] >= 10;
      parent.postMessage({
        type: "make_move",
        new_state: { scores, winner: won ? seat : null },
        next_turn_player_indices: won ? null : [(seat + 1) % latest.players.length],
      }, "*");
    }

    function render() {
      const s = stateOf(latest);
      const acting = latest.status === "ongoing" ? latest.acting_for_player_index : null;
      const name = (seat) => latest.players[seat].name;
      $("scores").textContent = latest.players.map((p, i) => `${p.name}: ${s.scores[i] ?? 0}`).join(" · ");
      $("status").textContent = s.winner !== null ? `${name(s.winner)} wins!`
        : acting === null ? "Waiting…" : `${name(acting)}'s turn`;
      const human = acting !== null && latest.players[acting].kind === "human";
      $("add").disabled = !human;
      $("add").onclick = () => move(acting);
      if (acting !== null && !human) {
        const scheduledFor = latest;
        setTimeout(() => { if (latest === scheduledFor) move(acting); }, 500);
      }
    }

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent || event.data?.type !== "state_changed") return;
      latest = event.data;
      sent = false;
      render();
    });
    </script>
    </body>
    </html>
    """
}
