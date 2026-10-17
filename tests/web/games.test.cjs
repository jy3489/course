const { test } = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  vm = require("node:vm"),
  path = require("node:path");
function logic(name) {
  const code = fs
    .readFileSync(path.join(__dirname, `../../games/playroom/${name}.html`), "utf8")
    .match(/<script>([\s\S]*?)<\/script>/)[1];
  return vm.runInNewContext(code + ";Logic", { structuredClone, Math });
}
const dots = logic("dots-and-boxes"),
  reversi = logic("reversi"),
  cards = logic("crazy-eights");
test("Dots: closes boxes, grants extra turn, rejects occupied edge, completes all 9 boxes", () => {
  let s = dots.initialState();
  for (const e of [0, 3, 12]) s = dots.play(s, s.turn, e).state;
  const seat = s.turn;
  s = dots.play(s, seat, 13).state;
  assert.equal(s.boxes[0], seat);
  assert.equal(s.turn, seat);
  assert.throws(() => dots.play(s, s.turn, 13));
  while (!s.done) s = dots.play(s, s.turn, dots.computer(s, s.turn)).state;
  assert.equal(s.boxes.filter((x) => x !== null).length, 9);
  assert.ok([0, 1].includes(s.winner));
});
test("Reversi: opening captures, illegal move, computer match reaches conserved final count", () => {
  let s = reversi.initialState();
  assert.equal(reversi.actions(s).length, 4);
  s = reversi.play(s, 0, 19).state;
  assert.equal(s.board[27], 0);
  assert.throws(() => reversi.play(s, 0, 0));
  let turns = 0;
  while (!s.done && turns++ < 64)
    s = reversi.play(s, s.turn, reversi.computer(s, s.turn)).state;
  assert.ok(s.done);
  assert.equal(s.board.filter((x) => x !== null).length, turns + 5);
});
test("Crazy Eights: unique 52 cards, matching, wild suit and winning last card", () => {
  let s = cards.initialState(4);
  assert.equal(new Set([...s.hands.flat(), ...s.deck, ...s.discard]).size, 52);
  assert.notEqual(cards.rank(s.discard[0]), 8);
  s = {
    hands: [[7], [14]],
    deck: [30],
    discard: [0],
    suit: 0,
    turn: 0,
    done: false,
    winner: null,
    passes: 0,
  };
  assert.throws(() => cards.play(s, 0, { card: 7 }));
  const result = cards.play(s, 0, { card: 7, chosenSuit: 2 });
  assert.equal(result.state.suit, 2);
  assert.equal(result.nextTurn, null);
  assert.equal(result.state.winner, 0);
});
test("Crazy Eights: deck recycling and 100 multiplayer bot games terminate without lost cards", () => {
  for (let n = 0; n < 100; n++) {
    let s = cards.initialState(2 + (n % 3)),
      turns = 0;
    while (!s.done && turns++ < 3000) {
      s = cards.play(s, s.turn, cards.computer(s, s.turn)).state;
      assert.equal(
        new Set([...s.hands.flat(), ...s.deck, ...s.discard]).size,
        52,
      );
    }
    assert.ok(s.done);
  }
});
