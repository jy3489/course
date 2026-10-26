"use strict";

// Tic-tac-toe server logic (v2 Game API). This is the Logic object the
// client used to run in the browser, moved to the server unchanged in
// spirit: moves are {cell}, the computer plays perfect minimax, and the
// match's token pot goes to a human winner — or is split among the
// humans on a draw or when a computer won.

const LINES = [
  [0, 1, 2], [3, 4, 5], [6, 7, 8],
  [0, 3, 6], [1, 4, 7], [2, 5, 8],
  [0, 4, 8], [2, 4, 6],
];
const MARKS = ["X", "O"];

function winningLine(board) {
  return (
    LINES.find(
      ([a, b, c]) => board[a] && board[a] === board[b] && board[a] === board[c]
    ) ?? null
  );
}

function initialState() {
  return { board: ["", "", "", "", "", "", "", "", ""], winner: null, draw: false };
}

/** Plays [cell] for [seat]; returns {state, nextTurn} (null when over). */
function play(state, seat, cell) {
  const current = state ?? initialState();
  if (current.winner !== null || current.draw) {
    throw new InvalidMove("the game is over");
  }
  if (current.board[cell] !== "") throw new InvalidMove("that cell is taken");
  const board = [...current.board];
  board[cell] = MARKS[seat];
  const line = winningLine(board);
  const draw = !line && board.every((mark) => mark !== "");
  const next = { board, winner: line ? seat : null, draw };
  return { state: next, nextTurn: line || draw ? null : 1 - seat };
}

/** Perfect play by minimax with alpha-beta pruning; ties broken by
 * [rng]. Pruning matters: a plain search of the empty board can
 * overrun the platform's 100 ms per-call limit in QuickJS on a loaded
 * machine (the publish gate's random walks found this on the live
 * server). The memo lives at module scope — the platform caches a
 * game's QuickJS context, so the table survives between calls; the
 * seat is part of the key because a terminal score is from [seat]'s
 * perspective and the shared table serves both seats. Only results
 * proven inside the search window are cached: a pruned node returns
 * a bound, not the exact score, and caching a bound would corrupt
 * later lookups. */
const minimaxMemo = new Map();

function computerCell(state, seat, rng) {
  const current = state ?? initialState();
  // Opening book: the first two plies are known theory, and searching
  // them is the expensive case (a fresh board is the largest search).
  // Every move below achieves the position's game-theoretic value —
  // tic-tac-toe is a draw under perfect play, and these are exactly
  // the drawing replies; the search decides everything after.
  const marks = current.board.filter((mark) => mark !== "").length;
  if (marks === 0) {
    const openings = [0, 2, 4, 6, 8]; // center and corners draw; edges lose
    return openings[Math.floor(rng() * openings.length)];
  }
  if (marks === 1) {
    if (current.board[4] === "") return 4; // answer a corner/edge with the center
    const corners = [0, 2, 6, 8]; // answer the center with a corner
    return corners[Math.floor(rng() * corners.length)];
  }
  const memo = minimaxMemo;
  const score = (board, toMove, alpha = -Infinity, beta = Infinity) => {
    const key = board.join(",") + "|" + toMove + "|" + seat;
    if (memo.has(key)) return memo.get(key);
    const alphaIn = alpha, betaIn = beta;
    const line = winningLine(board);
    let value;
    if (line) value = board[line[0]] === MARKS[seat] ? 1 : -1;
    else if (board.every((mark) => mark !== "")) value = 0;
    else if (toMove === seat) {
      value = -Infinity;
      for (let cell = 0; cell < 9; cell++) {
        if (board[cell]) continue;
        const next = [...board];
        next[cell] = MARKS[toMove];
        value = Math.max(value, score(next, 1 - toMove, alpha, beta));
        alpha = Math.max(alpha, value);
        if (alpha >= beta) break;
      }
    } else {
      value = Infinity;
      for (let cell = 0; cell < 9; cell++) {
        if (board[cell]) continue;
        const next = [...board];
        next[cell] = MARKS[toMove];
        value = Math.min(value, score(next, 1 - toMove, alpha, beta));
        beta = Math.min(beta, value);
        if (alpha >= beta) break;
      }
    }
    if (value > alphaIn && value < betaIn) memo.set(key, value);
    return value;
  };
  const options = current.board.flatMap((mark, cell) => {
    if (mark) return [];
    const next = [...current.board];
    next[cell] = MARKS[seat];
    return [{ cell, value: score(next, 1 - seat) }];
  });
  const best = Math.max(...options.map((option) => option.value));
  const bestCells = options.filter((option) => option.value === best);
  return bestCells[Math.floor(rng() * bestCells.length)].cell;
}

/** The token payouts when a match ends in [state]: the pot to a human
 * winner; split among the humans otherwise (a draw, or a computer won). */
function endPayouts(state, ctx) {
  const humans = ctx.players.filter((player) => player.kind === "human");
  if (humans.length === 0) return {};
  const winner =
    state.winner !== null ? ctx.players[state.winner] : undefined;
  if (winner && winner.kind === "human") {
    const payouts = {};
    for (const human of humans) payouts[human.id] = 0;
    payouts[winner.id] = ctx.pot;
    return payouts;
  }
  const payouts = {};
  const share = Math.floor(ctx.pot / humans.length);
  humans.forEach((human, index) => {
    payouts[human.id] =
      share + (index < ctx.pot - share * humans.length ? 1 : 0);
  });
  return payouts;
}

const game = {
  newMatch(ctx) {
    return { state: initialState(), turn: [ctx.players[0].id] };
  },

  applyMove(state, playerId, move, ctx) {
    if (
      !move ||
      typeof move !== "object" ||
      !Number.isInteger(move.cell) ||
      move.cell < 0 ||
      move.cell > 8
    ) {
      throw new InvalidMove("send {cell: 0..8}");
    }
    const seat = ctx.players.findIndex((player) => player.id === playerId);
    const { state: next, nextTurn } = play(state, seat, move.cell);
    const over = nextTurn === null;
    return {
      state: next,
      turn: over ? null : [ctx.players[nextTurn].id],
      ...(over ? { payouts: endPayouts(next, ctx) } : {}),
    };
  },

  computerMove(state, playerId, ctx) {
    const seat = ctx.players.findIndex((player) => player.id === playerId);
    return { cell: computerCell(state, seat, ctx.random) };
  },

  randomMove(state, playerId, ctx) {
    const empty = state.board.flatMap((mark, cell) => (mark === "" ? [cell] : []));
    return { cell: empty[Math.floor(ctx.random() * empty.length)] };
  },

  // No secrets in tic-tac-toe: the view is the state, plus the winning
  // line so the client can highlight it without reimplementing rules.
  viewFor(state, playerId, ctx) {
    return { ...state, line: winningLine(state.board) };
  },
};
