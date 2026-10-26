"use strict";

// Portal-only durable storage. Accounts use separate namespaces; credentials remain in Auth.
class PortalOffline {
  constructor(storage, request, user, online = () => navigator.onLine) {
    this.storage = storage;
    this.request = request;
    this.user = user;
    this.online = online;
    this.network = true;
    this.notice = "";
    this.retryAfter = 0;
  }
  key() {
    return `game-platform.portal.v1.${this.user()?.id || "visitor"}`;
  }
  read() {
    const raw = this.storage.getItem(this.key());
    return raw
      ? JSON.parse(raw)
      : { responses: {}, queues: {}, conflicts: {}, local: {} };
  }
  write(data) {
    try {
      this.storage.setItem(this.key(), JSON.stringify(data));
    } catch {
      throw Object.assign(
        new Error(
          "Device storage is full or unavailable. This move was not saved; free space and retry.",
        ),
        { storageFailure: true },
      );
    }
  }
  cached(path) {
    return this.read().responses[path];
  }
  save(path, value) {
    const data = this.read();
    data.responses[path] = value;
    this.write(data);
    return value;
  }
  decorate(match) {
    const data = this.read(),
      queue = data.queues[match.id] || [];
    let result = queue.length
      ? data.responses[`/matches/${match.id}`] || match
      : match;
    for (const item of queue) result = this.apply(result, item.body);
    return {
      ...result,
      pending: queue.length,
      conflict: data.conflicts[match.id] || null,
    };
  }
  apply(match, body) {
    // A v2 move ({move}) carries no resulting state: the server computes
    // it. The optimistic copy keeps the current view until the sync lands.
    if (!("new_state" in body)) {
      return { ...match, move_count: body.expected_move_count + 1 };
    }
    return {
      ...match,
      state: body.new_state,
      turn_of_player_indices: body.next_turn_player_indices,
      status: body.next_turn_player_indices === null ? "over" : "ongoing",
      end_reason: body.next_turn_player_indices === null ? "finished" : null,
      move_count: body.expected_move_count + 1,
    };
  }
  async get(path, options = {}) {
    const localId = path.startsWith("/matches/") ? path.slice(9) : null;
    if (localId?.startsWith("local-")) {
      const snapshot = localId.endsWith("/history");
      const history = localId.endsWith("/moves") || snapshot;
      const match = this.read().local[snapshot ? localId.slice(0, -8) : history ? localId.slice(0, -6) : localId];
      if (!match) throw new Error("Local practice is no longer saved.");
      if (snapshot) return {initial_state: null, initial_turn_player_indices: match.historyStart === 0 ? [0] : null, moves: match.moves || []};
      return history ? (match.moves || []) : match;
    }
    let value;
    const identity = this.key();
    try {
      if (!this.online() || Date.now() < this.retryAfter)
        throw Object.assign(new TypeError("Offline"), { cachedOnly: true });
      value = await this.request("GET", path, options);
      if (identity !== this.key())
        throw Object.assign(new Error("Account changed"), { status: 409 });
      this.network = true;
      const d = this.read();
      const matchId = /^\/matches\/([^/]+)$/.exec(path)?.[1];
      if (!(matchId && d.queues[matchId]?.length)) d.responses[path] = value;
      if (path === "/matches")
        for (const match of value)
          if (!d.queues[match.id]?.length)
            d.responses[`/matches/${match.id}`] = match;
      this.write(d);
    } catch (error) {
      if (error.storageFailure) throw error;
      if (
        error.status === 404 &&
        localId &&
        this.read().queues[localId]?.length
      )
        return this.decorate(this.cached(path));
      if (error.status && error.status < 500) throw error;
      this.network = false;
      if (!error.cachedOnly) this.retryAfter = Date.now() + 10000;
      value = this.cached(path);
      if (value === undefined)
        throw new Error(
          "Not saved on this device yet. Open this while online first.",
        );
    }
    if (path === "/matches") {
      const d = this.read(),
        ids = new Set(value.map((m) => m.id));
      const missing = Object.keys(d.queues)
        .filter((id) => !ids.has(id))
        .map((id) => d.responses[`/matches/${id}`])
        .filter(Boolean);
      return [...value, ...missing]
        .map((m) => this.decorate(m))
        .concat(Object.values(d.local));
    }
    if (path !== "/matches/open" && /^\/matches\/[^/]+$/.test(path))
      return this.decorate(value);
    return value;
  }
  archiveMatch(id) {
    const d = this.read();
    d.archivedMatches ||= [];
    d.archivedMatches.push({id, match:d.local[id] || d.responses[`/matches/${id}`], moves:d.queues[id] || [], conflict:d.conflicts[id] || null, savedAt:new Date().toISOString()});
    delete d.local[id];delete d.queues[id];delete d.conflicts[id];
    for (const key of Object.keys(d.responses)) {
      if (key === `/matches/${id}` || key.startsWith(`/matches/${id}/`)) delete d.responses[key];
      else if (Array.isArray(d.responses[key]) && key.startsWith("/matches")) d.responses[key] = d.responses[key].filter(match=>match.id!==id);
    }
    if (d.selectedId === id) d.selectedId = null;
    this.write(d);
  }
  endPractice(id) {
    const d = this.read(), match = d.local[id];
    if (!match || match.status !== "ongoing") return;
    match.resumeTurn = match.turn_of_player_indices;
    match.turn_of_player_indices = null;match.status = "over";match.end_reason = "player_left";
    this.write(d);
  }
  resumePractice(id) {
    const d = this.read(), match = d.local[id];
    if (!match?.resumeTurn) return;
    match.turn_of_player_indices = match.resumeTurn;delete match.resumeTurn;
    match.status = "ongoing";match.end_reason = null;this.write(d);
  }
  async api(method, path, options = {}) {
    const matchId = /^\/matches\/([^/]+)$/.exec(path)?.[1];
    if (method === "DELETE" && matchId?.startsWith("local-")) {this.archiveMatch(matchId);return null;}
    if (method === "GET") return this.get(path, options);
    if (!this.online())
      throw new Error(
        "This action needs a connection. Saved matches and local practice are available offline.",
      );
    const identity = this.key();
    let result;
    try {result = await this.request(method, path, options);}
    catch (error) {
      if (!(method === "DELETE" && matchId && error.status === 404 && this.cached(path)?.owner_user_id === this.user()?.id)) throw error;
    }
    if (identity !== this.key())
      throw new Error("Account changed; refresh to see the result.");
    if (method === "DELETE" && matchId) this.archiveMatch(matchId);
    if (result?.id && path.startsWith("/matches"))
      this.save(`/matches/${result.id}`, result);
    return result;
  }
  enqueue(match, body) {
    const data = this.read(),
      id = match.id;
    if (data.conflicts[id])
      throw new Error("Resolve the sync conflict before making another move.");
    if (id.startsWith("local-")) {
      if (data.local[id]?.move_count !== body.expected_move_count)
        throw new Error(
          "Local practice changed in another tab. Refresh before moving.",
        );
      const previous = data.local[id];
      data.local[id] = this.apply(previous, body);
      data.local[id].moves = [...(previous.moves || []), {new_state: body.new_state, next_turn_player_indices: body.next_turn_player_indices}];
      this.write(data);
      return data.local[id];
    }
    const base = data.responses[`/matches/${id}`] || match;
    const queue = data.queues[id] || [];
    if (base.move_count + queue.length !== body.expected_move_count)
      throw new Error(
        "This match changed in another tab. Refresh before moving.",
      );
    data.responses[`/matches/${id}`] = base;
    queue.push({
      body,
      baseState: match.state,
      baseTurn: match.turn_of_player_indices,
    });
    data.queues[id] = queue;
    this.write(data);
    return this.decorate(base);
  }
  async sync() {
    if (!this.online() || !this.user() || Date.now() < this.retryAfter) return;
    // Caller holds the origin-wide portal lock. Reload storage on every write.
    const account = this.user();
    const identity = this.key();
    const guard = () => {
      if (identity !== this.key())
        throw new Error("Account changed; sync paused.");
    };
    for (const id of Object.keys(this.read().queues)) {
      if (this.read().conflicts[id]) continue;
      let retries = 0;
      while (this.read().queues[id]?.length) {
        const item = this.read().queues[id][0],
          body = item.body;
        try {
          const remote = await this.request("GET", `/matches/${id}`, {
            user: account,
          });
          guard();
          this.network = true;
          let accepted = false;
          if (remote.move_count > body.expected_move_count) {
            const history = await this.request("GET", `/matches/${id}/moves`, {
              user: account,
            });
            guard();
            const previous = history[body.expected_move_count];
            accepted =
              previous?.made_by_user_id === account.id &&
              ("move" in body
                ? PortalOffline.equal(previous.move, body.move)
                : PortalOffline.equal(previous.new_state, body.new_state) &&
                  PortalOffline.equal(
                    previous.next_turn_player_indices,
                    body.next_turn_player_indices,
                  ));
            if (!accepted) {
              this.conflict(
                id,
                remote,
                "Another player changed this match. Your offline moves are saved for review.",
              );
              break;
            }
          } else if (
            remote.move_count !== body.expected_move_count ||
            remote.status !== "ongoing" ||
            !PortalOffline.equal(remote.state, item.baseState) ||
            !PortalOffline.equal(remote.turn_of_player_indices, item.baseTurn)
          ) {
            this.conflict(
              id,
              remote,
              "The match changed while offline. Review your saved moves before using the server version.",
            );
            break;
          }
          const updated = accepted
            ? remote
            : await this.request("POST", `/matches/${id}/moves`, {
                user: account,
                body,
              });
          guard();
          const data = this.read();
          data.queues[id].shift();
          data.responses[`/matches/${id}`] = updated;
          if (!data.queues[id].length) delete data.queues[id];
          this.write(data);
        } catch (error) {
          guard();
          if (error.status === 409) {
            if (++retries < 3) continue;
            this.conflict(
              id,
              null,
              "Repeated sync conflict. Review the server version before continuing.",
            );
            break;
          } // Re-read authoritative state before deciding whether to replay.
          if (error.status && error.status < 500) {
            this.conflict(id, null, `Sync stopped: ${error.message}`);
            break;
          }
          this.network = false;
          this.retryAfter = Date.now() + 10000;
          this.notice =
            "Connection unavailable. Moves remain saved on this device.";
          return;
        }
      }
    }
  }
  conflict(id, remote, message) {
    const d = this.read();
    d.conflicts[id] = { remote, message };
    this.write(d);
  }
  resolve(id) {
    const d = this.read();
    const conflict = d.conflicts[id];
    if (!conflict) return;
    // Keep an explicit archive rather than silently deleting a player's offline work.
    d.archives ??= [];
    d.archives.push({
      id,
      moves: d.queues[id],
      savedAt: new Date().toISOString(),
    });
    delete d.queues[id];
    delete d.conflicts[id];
    if (conflict.remote) d.responses[`/matches/${id}`] = conflict.remote;
    this.write(d);
  }
  practice(game, mode = "computer") {
    const user = this.user();
    if (!user)
      throw new Error("Log in online once before starting offline practice.");
    const count = Math.min(...game.allowed_player_counts),
      id = `local-${crypto.randomUUID()}`;
    const match = {
      id,
      game_id: game.id,
      game_version: game.version,
      owner_user_id: user.id,
      players: Array.from({ length: count }, (_, i) => ({
        player_index: i,
        kind: i === 0 || mode === "pass" ? "human" : "computer",
        user_id: i === 0 ? user.id : null,
      })),
      status: "ongoing",
      state: null,
      turn_of_player_indices: [0],
      move_count: 0,
      end_reason: null,
      created_at: new Date().toISOString(),
      localMode: mode,
      historyStart: 0,
    };
    const d = this.read();
    d.local[id] = match;
    d.responses[`/games/${game.id}/versions/${game.version}`] = game;
    this.write(d);
    return match;
  }
  static equal(a, b) {
    if (a === b) return true;
    if (!a || !b || typeof a !== "object" || typeof b !== "object")
      return false;
    const keys = Object.keys(a);
    return (
      keys.length === Object.keys(b).length &&
      keys.every((k) => Object.hasOwn(b, k) && PortalOffline.equal(a[k], b[k]))
    );
  }
}
if (typeof module !== "undefined") module.exports = PortalOffline;
