const game = {
  COLORS: ['red','blue','green','yellow','white'],
  newMatch(ctx) {
    const n = ctx.players.length;
    let deck = [];
    this.COLORS.forEach(c => { for (let v = 2; v <= 10; v++) deck.push({c, v}); });
    for (let i = deck.length - 1; i > 0; i--) {
      const j = Math.floor(ctx.random() * (i + 1));
      const t = deck[i]; deck[i] = deck[j]; deck[j] = t;
    }
    const hands = [], exps = [];
    for (let p = 0; p < n; p++) {
      const h = deck.splice(deck.length - 8, 8);
      this._sort(h);
      hands.push(h);
      const e = {};
      this.COLORS.forEach(c => e[c] = []);
      exps.push(e);
    }
    const discards = {};
    this.COLORS.forEach(c => discards[c] = []);
    const state = { deck, hands, exps, discards, phase: 'play', current: 0, last: null, turnNum: 0, scores: null, winner: null };
    return { state, turn: [ctx.players[0].id] };
  },
  applyMove(state, playerId, move, ctx) {
    const idx = ctx.players.findIndex(p => p.id === playerId);
    if (idx === -1) throw new InvalidMove('not seated');
    if (state.phase === 'over') throw new InvalidMove('game over');
    if (idx !== state.current) throw new InvalidMove('not your turn');
    if (!move || typeof move !== 'object') throw new InvalidMove('bad move');
    if (move.kind === 'play') {
      if (state.phase !== 'play') throw new InvalidMove('must draw');
      const hi = move.handIndex;
      const hand = state.hands[idx];
      if (!Number.isInteger(hi) || hi < 0 || hi >= hand.length) throw new InvalidMove('bad card');
      const card = hand[hi];
      if (move.color !== card.c) throw new InvalidMove('must match color');
      if (move.dest === 'exp') {
        const col = state.exps[idx][card.c];
        if (col.length && !(card.v > col[col.length-1].v)) throw new InvalidMove('must be ascending');
        hand.splice(hi, 1);
        col.push(card);
        state.last = { p: idx, text: ctx.players[idx].display_name + ' played ' + card.v + ' ' + card.c };
      } else if (move.dest === 'discard') {
        hand.splice(hi, 1);
        state.discards[card.c].push(card);
        state.last = { p: idx, text: ctx.players[idx].display_name + ' discarded ' + card.v + ' ' + card.c };
      } else throw new InvalidMove('bad destination');
      state.phase = 'draw';
      return { state, turn: [playerId] };
    }
    if (move.kind === 'draw') {
      if (state.phase !== 'draw') throw new InvalidMove('must play first');
      const src = move.source;
      if (src === 'deck') {
        if (!state.deck.length) throw new InvalidMove('deck empty');
        const c = state.deck.pop();
        state.hands[idx].push(c);
        this._sort(state.hands[idx]);
        state.last = { p: idx, text: ctx.players[idx].display_name + ' drew from deck' };
      } else if (this.COLORS.indexOf(src) !== -1) {
        const pile = state.discards[src];
        if (!pile.length) throw new InvalidMove('pile empty');
        const c = pile.pop();
        state.hands[idx].push(c);
        this._sort(state.hands[idx]);
        state.last = { p: idx, text: ctx.players[idx].display_name + ' drew ' + c.v + ' ' + c.c + ' from discard' };
      } else throw new InvalidMove('bad source');
      if (state.deck.length === 0) {
        const scores = this._scores(state);
        const w = this._winner(scores);
        state.scores = scores; state.winner = w; state.phase = 'over';
        return { state, turn: null, payouts: this._payouts(ctx, scores, w) };
      }
      state.phase = 'play';
      state.turnNum = (state.turnNum || 0) + 1;
      const n = ctx.players.length;
      state.current = (idx + 1) % n;
      return { state, turn: [ctx.players[state.current].id] };
    }
    throw new InvalidMove('unknown move');
  },
  computerMove(state, playerId, ctx) {
    const idx = ctx.players.findIndex(p => p.id === playerId);
    if (state.phase === 'draw') {
      let bestC = null, bestV = -1;
      this.COLORS.forEach(c => {
        const pile = state.discards[c];
        if (pile && pile.length) {
          const top = pile[pile.length-1];
          const col = state.exps[idx][c];
          const ok = !col.length || top.v > col[col.length-1].v;
          if (ok && top.v >= 6 && top.v > bestV) { bestV = top.v; bestC = c; }
        }
      });
      if (bestC) return { kind: 'draw', source: bestC };
      if (state.deck.length) return { kind: 'draw', source: 'deck' };
      let bc = null, bv = -1;
      this.COLORS.forEach(c => { const p = state.discards[c]; if (p && p.length && p[p.length-1].v > bv) { bv = p[p.length-1].v; bc = c; } });
      return { kind: 'draw', source: bc };
    }
    const hand = state.hands[idx], exp = state.exps[idx];
    let best = -1, bestV = -1;
    for (let i = 0; i < hand.length; i++) {
      const c = hand[i];
      const col = exp[c.c];
      if (col.length && c.v > col[col.length-1].v) { if (c.v > bestV) { bestV = c.v; best = i; } }
    }
    if (best >= 0) return { kind: 'play', handIndex: best, dest: 'exp', color: hand[best].c };
    let start = -1, startV = -1;
    for (let i = 0; i < hand.length; i++) {
      const c = hand[i];
      if (exp[c.c].length === 0 && c.v >= 7) { if (c.v > startV) { startV = c.v; start = i; } }
    }
    if (start >= 0) return { kind: 'play', handIndex: start, dest: 'exp', color: hand[start].c };
    let di = 0;
    for (let i = 1; i < hand.length; i++) if (hand[i].v < hand[di].v) di = i;
    return { kind: 'play', handIndex: di, dest: 'discard', color: hand[di].c };
  },
  randomMove(state, playerId, ctx) {
    const idx = ctx.players.findIndex(p => p.id === playerId);
    if (state.phase === 'draw') {
      const opts = [];
      if (state.deck.length) opts.push('deck');
      this.COLORS.forEach(c => { if (state.discards[c].length) opts.push(c); });
      const s = opts[Math.floor(ctx.random() * opts.length)];
      return { kind: 'draw', source: s };
    }
    const hand = state.hands[idx];
    const hi = Math.floor(ctx.random() * hand.length);
    const card = hand[hi];
    const col = state.exps[idx][card.c];
    const ok = !col.length || card.v > col[col.length-1].v;
    if (ok && ctx.random() < 0.5) return { kind: 'play', handIndex: hi, dest: 'exp', color: card.c };
    return { kind: 'play', handIndex: hi, dest: ok && ctx.random() < 0.5 ? 'exp' : 'discard', color: card.c };
  },
  viewFor(state, playerId, ctx) {
    let myIdx = -1;
    if (playerId !== null && playerId !== undefined) myIdx = ctx.players.findIndex(p => p.id === playerId);
    if (myIdx < 0) myIdx = 0;
    const handCounts = state.hands.map(h => h.length);
    let hand = [];
    const me = ctx.players.findIndex(p => p.id === playerId);
    if (me >= 0 && state.hands[me]) hand = state.hands[me].map(c => ({ c: c.c, v: c.v }));
    else if (state.phase === 'over' && (playerId === null || playerId === undefined)) hand = (state.hands[state.current]||[]).map(c => ({ c: c.c, v: c.v }));
    return {
      hand, handCounts,
      exps: JSON.parse(JSON.stringify(state.exps)),
      discards: JSON.parse(JSON.stringify(state.discards)),
      deckCount: state.deck.length,
      phase: state.phase, current: state.current,
      last: state.last, scores: state.scores, winner: state.winner,
      myIndex: myIdx
    };
  },
  _sort(h) {
    const o = { red: 0, blue: 1, green: 2, yellow: 3, white: 4 };
    h.sort((a, b) => (o[a.c] - o[b.c]) || (a.v - b.v));
  },
  _scores(state) {
    return state.exps.map(exp => {
      let t = 0;
      this.COLORS.forEach(c => {
        const a = exp[c];
        if (a && a.length) { let s = 0; for (let k = 0; k < a.length; k++) s += a[k].v; t += s - 20; }
      });
      return t;
    });
  },
  _winner(scores) {
    let m = -Infinity, w = 0;
    scores.forEach((s, i) => { if (s > m) { m = s; w = i; } });
    return w;
  },
  _payouts(ctx, scores, w) {
    const pot = ctx.pot || 0;
    const out = {};
    const humans = ctx.players.filter(p => p.kind === 'human');
    if (pot <= 0 || !humans.length) return out;
    const wId = ctx.players[w] ? ctx.players[w].id : null;
    const wIsHuman = humans.some(h => h.id === wId);
    if (wIsHuman) { out[wId] = pot; return out; }
    let bh = humans[0].id, bs = -Infinity;
    humans.forEach(h => {
      const i = ctx.players.findIndex(p => p.id === h.id);
      if (scores[i] > bs) { bs = scores[i]; bh = h.id; }
    });
    out[bh] = pot;
    return out;
  }
};