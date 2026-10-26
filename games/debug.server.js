"use strict";

// Debug game server logic (v2 Game API). The debug game has no rules of
// its own, exactly as when its client drove the match: a move IS the next
// state — {new_state, next_turn_player_indices, payouts?} — with seat
// indices translated to player ids here. It exists to exercise the
// platform, so what it validates is only the move's shape.
//
// Computer players pass the turn to the next seat. Passes by computers
// are counted in state.passes, and after MAX_COMPUTER_PASSES of them the
// match ends, so random-walk tests of all-computer matches terminate.

const MAX_COMPUTER_PASSES = 20;

function seatOf(ctx, playerId) {
  return ctx.players.findIndex((player) => player.id === playerId);
}

/** Splits the pot equally among the human players. */
function defaultPayouts(ctx) {
  const humans = ctx.players.filter((player) => player.kind === "human");
  if (humans.length === 0) return {};
  const payouts = {};
  const share = Math.floor(ctx.pot / humans.length);
  humans.forEach((human, index) => {
    payouts[human.id] =
      share + (index < ctx.pot - share * humans.length ? 1 : 0);
  });
  return payouts;
}

/** A move's payouts, keyed by seat index, translated to player ids. */
function movePayouts(move, ctx) {
  if (!move.payouts || typeof move.payouts !== "object") return null;
  const payouts = {};
  for (const [seat, amount] of Object.entries(move.payouts)) {
    const player = ctx.players[Number(seat)];
    if (!player || !Number.isInteger(amount) || amount < 0) {
      throw new InvalidMove(
        "payouts must map seat indices to non-negative integers"
      );
    }
    payouts[player.id] = amount;
  }
  return payouts;
}

const game = {
  newMatch(ctx) {
    return { state: { counter: 0, note: "" }, turn: [ctx.players[0].id] };
  },

  applyMove(state, playerId, move, ctx) {
    if (!move || typeof move !== "object" || !("new_state" in move)) {
      throw new InvalidMove("send {new_state, next_turn_player_indices}");
    }
    const next = move.next_turn_player_indices;
    if (
      !(
        next === null ||
        (Array.isArray(next) &&
          next.length > 0 &&
          next.every(
            (seat) =>
              Number.isInteger(seat) && seat >= 0 && seat < ctx.players.length
          ))
      )
    ) {
      throw new InvalidMove(
        "next_turn_player_indices must be null or a non-empty list of seat indices"
      );
    }
    let newState = move.new_state;
    const mover = ctx.players[seatOf(ctx, playerId)];
    if (
      mover &&
      mover.kind === "computer" &&
      newState !== null &&
      typeof newState === "object" &&
      !Array.isArray(newState)
    ) {
      const passes = (Number.isInteger(newState.passes) ? newState.passes : 0) + 1;
      newState = { ...newState, passes };
      if (passes >= MAX_COMPUTER_PASSES && next !== null) {
        return {
          state: newState,
          turn: null,
          payouts: movePayouts(move, ctx) ?? defaultPayouts(ctx),
        };
      }
    }
    const over = next === null;
    return {
      state: newState,
      turn: over ? null : next.map((seat) => ctx.players[seat].id),
      ...(over ? { payouts: movePayouts(move, ctx) ?? defaultPayouts(ctx) } : {}),
    };
  },

  computerMove(state, playerId, ctx) {
    const nextSeat = (seatOf(ctx, playerId) + 1) % ctx.players.length;
    return { new_state: state, next_turn_player_indices: [nextSeat] };
  },

  randomMove(state, playerId, ctx) {
    return game.computerMove(state, playerId, ctx);
  },

  // The debug game exists to show everything: the view is the state.
  viewFor(state, playerId, ctx) {
    return state;
  },
};
