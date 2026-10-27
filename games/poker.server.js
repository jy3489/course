"use strict";

// Texas hold'em server logic (v2 Game API). This is the Logic object the
// client used to run in the browser, moved to the server: the state
// shape is unchanged, so the client only had to stop computing it. The
// deck is shuffled with ctx.random, so every match replays exactly, and
// viewFor keeps the deck and the other players' hole cards on the
// server — a client only ever sees its own cards (and what a showdown
// revealed, in previous_hand).
//
// A move is an action: {type: "fold" | "check" | "call" | "raise", to?}.
// The match runs hand after hand until one seat has all the chips; the
// token pot then goes to that seat if it is human, and is split among
// the humans otherwise. Chips are not tokens: leaving mid-match pays out
// of the token pot in proportion to the leaver's share of the chips.

const RANKS = "23456789TJQKA";
const SUITS = "shdc";
const HAND_NAMES = [
  "high card", "pair", "two pair", "three of a kind", "straight", "flush",
  "full house", "four of a kind", "straight flush",
];
const STARTING_CHIPS = 1000;
const SMALL_BLIND = 10;
const BIG_BLIND = 20;
// Blinds double every this many hands, as in tournaments, so games end.
const HANDS_PER_BLIND_LEVEL = 10;
const LOG_LENGTH = 10;

const clone = (value) => JSON.parse(JSON.stringify(value));
const rankOf = (card) => RANKS.indexOf(card[0]);
const sum = (values) => values.reduce((total, value) => total + value, 0);

function shuffledDeck(rng) {
  const deck = [];
  for (const rank of RANKS) for (const suit of SUITS) deck.push(rank + suit);
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

// Hand ranking: [category, ...tiebreakers], compared lexicographically.
function rank5(cards) {
  const ranks = cards.map(rankOf).sort((a, b) => b - a);
  const counts = new Map();
  for (const rank of ranks) counts.set(rank, (counts.get(rank) ?? 0) + 1);
  const groups = [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0]);
  const flush = cards.every((card) => card[1] === cards[0][1]);
  const unique = [...new Set(ranks)];
  let straightHigh = null;
  if (unique.length === 5) {
    if (unique[0] - unique[4] === 4) straightHigh = unique[0];
    else if (unique.join() === "12,3,2,1,0") straightHigh = 3; // A-2-3-4-5
  }
  const byGroups = groups.map(([rank]) => rank);
  if (straightHigh !== null && flush) return [8, straightHigh];
  if (groups[0][1] === 4) return [7, ...byGroups];
  if (groups[0][1] === 3 && groups[1][1] === 2) return [6, ...byGroups];
  if (flush) return [5, ...ranks];
  if (straightHigh !== null) return [4, straightHigh];
  if (groups[0][1] === 3) return [3, ...byGroups];
  if (groups[0][1] === 2 && groups[1][1] === 2) return [2, ...byGroups];
  if (groups[0][1] === 2) return [1, ...byGroups];
  return [0, ...ranks];
}

function compare(a, b) {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] ?? -1) !== (b[i] ?? -1)) return (a[i] ?? -1) - (b[i] ?? -1);
  }
  return 0;
}

function bestHand(cards) {
  let best = null;
  for (let a = 0; a < cards.length; a++)
    for (let b = a + 1; b < cards.length; b++)
      for (let c = b + 1; c < cards.length; c++)
        for (let d = c + 1; d < cards.length; d++)
          for (let e = d + 1; e < cards.length; e++) {
            const value = rank5([cards[a], cards[b], cards[c], cards[d], cards[e]]);
            if (best === null || compare(value, best) > 0) best = value;
          }
  return best;
}

const handName = (value) => HAND_NAMES[value[0]];

// Seats are indexes into ctx.players. Seats added by players joining
// mid-match get chips and play from the next hand.
function ensureSeats(state, numSeats) {
  const s = clone(state);
  while (s.chips.length < numSeats) {
    s.chips.push(STARTING_CHIPS);
    s.hole.push(null);
    s.in_hand.push(false);
    s.bets.push(0);
    s.contributed.push(0);
    s.needs_action.push(false);
  }
  return s;
}

function nextSeat(s, from, predicate) {
  const n = s.chips.length;
  for (let step = 1; step <= n; step++) {
    const seat = (((from + step) % n) + n) % n;
    if (predicate(seat)) return seat;
  }
  return null;
}

const canAct = (s, seat) => s.in_hand[seat] && s.chips[seat] > 0;

function log(s, text) {
  s.log = [...s.log, text].slice(-LOG_LENGTH);
}

function post(s, seat, amount) {
  const paid = Math.min(amount, s.chips[seat]);
  s.chips[seat] -= paid;
  s.bets[seat] += paid;
  s.contributed[seat] += paid;
  return paid;
}

function newGame(numSeats, rng) {
  const s = {
    small_blind: SMALL_BLIND,
    big_blind: BIG_BLIND,
    chips: Array(numSeats).fill(STARTING_CHIPS),
    hand_number: 0,
    dealer: -1,
    deck: [],
    hole: Array(numSeats).fill(null),
    board: [],
    street: null,
    in_hand: Array(numSeats).fill(false),
    bets: Array(numSeats).fill(0),
    contributed: Array(numSeats).fill(0),
    current_bet: 0,
    min_raise: BIG_BLIND,
    needs_action: Array(numSeats).fill(false),
    to_act: null,
    winner: null,
    previous_hand: null,
    log: [],
  };
  return startHand(s, rng);
}

// Deals a new hand, or ends the game when only one seat has chips left.
function startHand(s, rng) {
  const n = s.chips.length;
  const active = [...Array(n).keys()].filter((seat) => s.chips[seat] > 0);
  s.hole = Array(n).fill(null);
  s.in_hand = Array(n).fill(false);
  s.bets = Array(n).fill(0);
  s.contributed = Array(n).fill(0);
  s.needs_action = Array(n).fill(false);
  s.board = [];
  s.deck = [];
  if (active.length < 2) {
    s.winner = active.length ? active[0] : null;
    s.street = null;
    s.to_act = null;
    return s;
  }
  s.hand_number += 1;
  const level = Math.floor((s.hand_number - 1) / HANDS_PER_BLIND_LEVEL);
  s.small_blind = SMALL_BLIND * 2 ** level;
  s.big_blind = BIG_BLIND * 2 ** level;
  s.dealer = nextSeat(s, s.dealer, (seat) => s.chips[seat] > 0);
  s.deck = shuffledDeck(rng);
  for (const seat of active) {
    s.in_hand[seat] = true;
    s.hole[seat] = [s.deck.pop(), s.deck.pop()];
  }
  s.street = "preflop";
  const headsUp = active.length === 2;
  const sb = headsUp ? s.dealer : nextSeat(s, s.dealer, (seat) => s.in_hand[seat]);
  const bb = nextSeat(s, sb, (seat) => s.in_hand[seat]);
  post(s, sb, s.small_blind);
  post(s, bb, s.big_blind);
  s.current_bet = Math.max(...s.bets);
  s.min_raise = s.big_blind;
  s.needs_action = s.in_hand.map((inHand, seat) => inHand && s.chips[seat] > 0);
  log(s, `Hand #${s.hand_number}: seat ${s.dealer + 1} deals; blinds ${s.small_blind}/${s.big_blind}.`);
  return advance(s, bb, rng);
}

function legal(s, seat) {
  const toCall = s.current_bet - s.bets[seat];
  const maxRaiseTo = s.bets[seat] + s.chips[seat];
  const canRaise = maxRaiseTo > s.current_bet;
  return {
    canFold: toCall > 0,
    canCheck: toCall === 0,
    callAmount: Math.min(toCall, s.chips[seat]),
    minRaiseTo: canRaise ? Math.min(s.current_bet + s.min_raise, maxRaiseTo) : null,
    maxRaiseTo: canRaise ? maxRaiseTo : null,
  };
}

/**
 * Applies [action] ({type: "fold" | "check" | "call" | "raise", to?}) by
 * [seat]. Throws a plain Error with a player-facing message for an
 * illegal action; applyMove turns those into InvalidMove.
 * Returns {state, nextTurn}; nextTurn is null when the game is over.
 */
function act(state, seat, action, rng, names = null) {
  if (state.to_act !== seat) {
    throw new Error(`it's seat ${state.to_act}'s turn, not ${seat}'s`);
  }
  const s = clone(state);
  const who = names ? names[seat] : `Seat ${seat + 1}`;
  const options = legal(s, seat);
  switch (action.type) {
    case "fold":
      s.in_hand[seat] = false;
      log(s, `${who} folds.`);
      break;
    case "check":
      if (!options.canCheck) throw new Error("can't check facing a bet");
      log(s, `${who} checks.`);
      break;
    case "call":
      if (options.canCheck) throw new Error("nothing to call");
      post(s, seat, options.callAmount);
      log(s, `${who} calls ${options.callAmount}${s.chips[seat] === 0 ? " (all in)" : ""}.`);
      break;
    case "raise": {
      const to = Math.floor(action.to);
      if (options.minRaiseTo === null || !(to >= options.minRaiseTo && to <= options.maxRaiseTo)) {
        throw new Error(`raise must be to between ${options.minRaiseTo} and ${options.maxRaiseTo}`);
      }
      const raiseBy = to - s.current_bet;
      if (raiseBy >= s.min_raise) s.min_raise = raiseBy;
      post(s, seat, to - s.bets[seat]);
      s.current_bet = to;
      for (let other = 0; other < s.chips.length; other++) {
        if (other !== seat && canAct(s, other)) s.needs_action[other] = true;
      }
      log(s, `${who} raises to ${to}${s.chips[seat] === 0 ? " (all in)" : ""}.`);
      break;
    }
    default:
      throw new Error(`unknown action ${action.type}`);
  }
  s.needs_action[seat] = false;
  const next = advance(s, seat, rng, names);
  return { state: next, nextTurn: next.winner !== null ? null : next.to_act };
}

// Moves on after a seat acted: next player, next street, showdown or next hand.
function advance(s, lastSeat, rng, names = null) {
  const n = s.chips.length;
  const seats = [...Array(n).keys()];
  const remaining = seats.filter((seat) => s.in_hand[seat]);
  if (remaining.length === 1) {
    const won = sum(s.contributed);
    const winner = remaining[0];
    return endHand(s, [{ seat: winner, amount: won }], {
      hand_number: s.hand_number, board: s.board, shown: [],
      won: [{ seat: winner, amount: won }], description: "everyone else folded",
    }, rng, names);
  }
  const pending = seats.filter(
    (seat) => canAct(s, seat) && (s.needs_action[seat] || s.bets[seat] < s.current_bet));
  if (pending.length > 0) {
    s.to_act = nextSeat(s, lastSeat, (seat) => pending.includes(seat));
    return s;
  }
  // The betting round is over: deal the next street, or run the board out when at
  // most one player can still bet.
  const actors = seats.filter((seat) => canAct(s, seat));
  do {
    if (s.street === "river") return showdown(s, rng, names);
    const count = s.street === "preflop" ? 3 : 1;
    s.street = { preflop: "flop", flop: "turn", turn: "river" }[s.street];
    for (let i = 0; i < count; i++) s.board.push(s.deck.pop());
    log(s, `${s.street[0].toUpperCase() + s.street.slice(1)}: ${s.board.join(" ")}`);
  } while (actors.length < 2);
  s.bets = Array(n).fill(0);
  s.current_bet = 0;
  s.min_raise = s.big_blind;
  s.needs_action = seats.map((seat) => canAct(s, seat));
  s.to_act = nextSeat(s, s.dealer, (seat) => canAct(s, seat));
  return s;
}

function showdown(s, rng, names) {
  const seats = [...Array(s.chips.length).keys()];
  const contenders = seats.filter((seat) => s.in_hand[seat]);
  const values = new Map(contenders.map((seat) => [seat, bestHand([...s.hole[seat], ...s.board])]));
  const levels = [...new Set(s.contributed.filter((amount) => amount > 0))].sort((a, b) => a - b);
  const won = new Map();
  let previous = 0;
  for (const level of levels) {
    const amount = sum(s.contributed.map((c) => Math.min(c, level) - Math.min(c, previous)));
    let eligible = contenders.filter((seat) => s.contributed[seat] >= level);
    if (eligible.length === 0) eligible = contenders;
    previous = level;
    const best = eligible.reduce((a, b) => (compare(values.get(a), values.get(b)) >= 0 ? a : b));
    // Split ties evenly; odd chips go to the first winner after the dealer.
    const winners = eligible
      .filter((seat) => compare(values.get(seat), values.get(best)) === 0)
      .sort((a, b) => ((a - s.dealer - 1 + seats.length) % seats.length) - ((b - s.dealer - 1 + seats.length) % seats.length));
    const share = Math.floor(amount / winners.length);
    winners.forEach((seat, i) => {
      won.set(seat, (won.get(seat) ?? 0) + share + (i === 0 ? amount - share * winners.length : 0));
    });
  }
  const wins = [...won.entries()].map(([seat, amount]) => ({ seat, amount }));
  const best = [...values.values()].reduce((a, b) => (compare(a, b) >= 0 ? a : b));
  return endHand(s, wins, {
    hand_number: s.hand_number,
    board: s.board,
    shown: contenders.map((seat) => ({ seat, cards: s.hole[seat], hand: handName(values.get(seat)) })),
    won: wins,
    description: `showdown, won with ${handName(best)}`,
  }, rng, names);
}

function endHand(s, wins, summary, rng, names) {
  for (const { seat, amount } of wins) {
    s.chips[seat] += amount;
    const who = names ? names[seat] : `Seat ${seat + 1}`;
    log(s, `${who} wins ${amount} (${summary.description}).`);
  }
  s.previous_hand = summary;
  return startHand(s, rng);
}

/** A simple strategy: play strong hands, call cheap, fold weak hands facing bets. */
function computerAction(s, seat, rng) {
  const options = legal(s, seat);
  const hole = s.hole[seat];
  let strength;
  if (s.board.length === 0) {
    const [hi, lo] = hole.map(rankOf).sort((a, b) => b - a);
    strength = hi === lo ? 0.55 + hi / 30 : (hi + lo) / 40 + (hole[0][1] === hole[1][1] ? 0.05 : 0);
  } else {
    const category = bestHand([...hole, ...s.board])[0];
    strength = [0.15, 0.45, 0.65, 0.75, 0.85, 0.88, 0.93, 0.97, 0.99][category];
  }
  strength += (rng() - 0.5) * 0.1;
  const pot = sum(s.contributed);
  const raiseTo = () => Math.min(
    options.maxRaiseTo, Math.max(options.minRaiseTo, s.current_bet + Math.round(pot / 2)));
  if (options.canCheck) {
    if (strength > 0.6 && options.minRaiseTo !== null && rng() < 0.6) return { type: "raise", to: raiseTo() };
    return { type: "check" };
  }
  if (strength > 0.8 && options.minRaiseTo !== null && rng() < 0.5) return { type: "raise", to: raiseTo() };
  const potOdds = options.callAmount / (pot + options.callAmount);
  if (strength > potOdds + 0.1 || (options.callAmount <= s.big_blind && strength > 0.25)) {
    return { type: "call" };
  }
  return { type: "fold" };
}

/** A random legal action, biased to all-in raises so random-walk test
 * matches end quickly instead of grinding through hundreds of hands. */
function randomAction(s, seat, rng) {
  const options = legal(s, seat);
  if (options.minRaiseTo !== null && rng() < 0.5) {
    return { type: "raise", to: options.maxRaiseTo };
  }
  const choices = [];
  if (options.canCheck) choices.push({ type: "check" });
  if (options.canFold) choices.push({ type: "fold" });
  if (!options.canCheck) choices.push({ type: "call" });
  if (options.minRaiseTo !== null) choices.push({ type: "raise", to: options.minRaiseTo });
  return choices[Math.floor(rng() * choices.length)];
}

/** The token payouts when the game is won: the pot to the winning seat
 * if it is human; split among the humans otherwise. */
function endPayouts(state, ctx) {
  const humans = ctx.players.filter((player) => player.kind === "human");
  if (humans.length === 0) return {};
  const winner = state.winner !== null ? ctx.players[state.winner] : undefined;
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

function seatOf(ctx, playerId) {
  return ctx.players.findIndex((player) => player.id === playerId);
}

function namesOf(ctx) {
  return ctx.players.map((player) => player.display_name);
}

function turnOf(state, ctx) {
  if (state.winner !== null || state.to_act === null) return null;
  return [ctx.players[state.to_act].id];
}

const game = {
  newMatch(ctx) {
    const state = newGame(ctx.players.length, ctx.random);
    return { state, turn: turnOf(state, ctx) };
  },

  applyMove(state, playerId, move, ctx) {
    if (
      !move ||
      typeof move !== "object" ||
      !["fold", "check", "call", "raise"].includes(move.type) ||
      (move.type === "raise" && typeof move.to !== "number")
    ) {
      throw new InvalidMove(
        'send an action: {type: "fold"|"check"|"call"|"raise", to?}'
      );
    }
    const seat = seatOf(ctx, playerId);
    let result;
    try {
      result = act(state, seat, move, ctx.random, namesOf(ctx));
    } catch (error) {
      // act's errors are all player-facing rule violations.
      throw new InvalidMove(error.message);
    }
    const over = result.nextTurn === null;
    return {
      state: result.state,
      turn: over ? null : [ctx.players[result.nextTurn].id],
      ...(over ? { payouts: endPayouts(result.state, ctx) } : {}),
    };
  },

  computerMove(state, playerId, ctx) {
    return computerAction(state, seatOf(ctx, playerId), ctx.random);
  },

  randomMove(state, playerId, ctx) {
    return randomAction(state, seatOf(ctx, playerId), ctx.random);
  },

  viewFor(state, playerId, ctx) {
    const view = clone(state);
    // The deck never leaves the server, and neither do other players'
    // hole cards; what a showdown revealed is in previous_hand.
    view.deck = [];
    // Which seats were dealt into this hand is public (their cards are
    // not); the client needs it to tell "folded" from "joins next hand",
    // since hidden hole cards are null in the view either way.
    view.dealt_in = state.hole.map((cards) => cards !== null);
    const seat = playerId === null ? -1 : seatOf(ctx, playerId);
    view.hole = state.hole.map((cards, index) => (index === seat ? cards : null));
    view.your_seat = seat === -1 ? null : seat;
    view.legal =
      seat !== -1 && state.to_act === seat && state.winner === null
        ? legal(state, seat)
        : null;
    return view;
  },

  playerJoined(state, playerId, ctx) {
    // An appended seat gets chips and plays from the next hand. A seat
    // taken over from a computer may be mid-hand: the new player sits
    // the rest of that hand out — the seat's bets stay in the pot, its
    // cards are mucked unseen — and is dealt in from the next hand.
    const grown = ensureSeats(state, ctx.players.length);
    const seat = seatOf(ctx, playerId);
    if (seat === -1 || !grown.in_hand[seat]) {
      return { state: grown, turn: turnOf(grown, ctx) };
    }
    grown.in_hand[seat] = false;
    grown.hole[seat] = null;
    log(grown, `${namesOf(ctx)[seat]} joins and sits out until the next hand.`);
    if (grown.to_act !== seat) {
      return { state: grown, turn: turnOf(grown, ctx) };
    }
    const next = advance(grown, seat, ctx.random, namesOf(ctx));
    return { state: next, turn: turnOf(next, ctx) };
  },

  playerLeft(state, playerId, ctx) {
    const seat = seatOf(ctx, playerId);
    const totalChips = sum(state.chips) + sum(state.contributed);
    const stack = state.chips[seat] + state.contributed[seat];
    const payout =
      totalChips > 0 ? Math.floor((ctx.pot * stack) / totalChips) : 0;
    const s = clone(state);
    let next = s;
    if (s.in_hand[seat]) {
      s.in_hand[seat] = false;
      log(s, `${namesOf(ctx)[seat]} leaves.`);
      if (s.to_act === seat) next = advance(s, seat, ctx.random, namesOf(ctx));
    }
    return { state: next, turn: turnOf(next, ctx), payout };
  },
};
