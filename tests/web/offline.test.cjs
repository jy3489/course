const { test } = require("node:test"),
  assert = require("node:assert/strict");
const Offline = require("../../static/portal-offline.js");
const base = {
  id: "match",
  state: null,
  turn_of_player_indices: [0],
  status: "ongoing",
  move_count: 0,
};
const body = {
  new_state: { score: 1 },
  next_turn_player_indices: [1],
  expected_move_count: 0,
};
function setup(request) {
  const map = new Map();
  const storage = {
    getItem: (k) => map.get(k) || null,
    setItem: (k, v) => map.set(k, v),
  };
  let user = { id: "alice", password: "test" },
    online = false;
  const o = new Offline(
    storage,
    request,
    () => user,
    () => online,
  );
  o.save("/matches/match", base);
  return {
    o,
    storage,
    setOnline: (v) => (online = v),
    setUser: (v) => (user = v),
  };
}
test("durable offline moves survive reload and accounts stay isolated", () => {
  const { o, storage, setUser } = setup();
  o.enqueue(base, body);
  const reloaded = new Offline(
    storage,
    () => {},
    () => ({ id: "alice" }),
  );
  assert.equal(reloaded.decorate(base).move_count, 1);
  assert.equal(reloaded.decorate(base).pending, 1);
  setUser({ id: "bob" });
  assert.equal(o.cached("/matches/match"), undefined);
  assert.deepEqual(o.read().queues, {});
});
test("commit with lost response is recognized from history, never double submitted", async () => {
  let remote = base,
    posts = 0;
  const { o, setOnline } = setup(async (method, path, opt) => {
    if (path.endsWith("/moves") && method === "GET")
      return [{ made_by_user_id: "alice", ...body }];
    if (method === "GET") return remote;
    posts++;
    remote = o.apply(remote, body);
    throw new TypeError("lost acknowledgement");
  });
  o.enqueue(base, body);
  setOnline(true);
  await o.sync();
  assert.equal(o.decorate(base).pending, 1);
  await o.sync();
  assert.equal(posts, 1);
  o.retryAfter = 0;
  await o.sync();
  assert.equal(posts, 1);
  assert.equal(o.cached("/matches/match").move_count, 1);
  assert.deepEqual(o.read().queues, {});
});
test("a forced sync ignores the retry backoff; a background sync respects it", async () => {
  let remote = base,
    posts = 0;
  const v2body = { move: { type: "call" }, expected_move_count: 0 };
  const { o, setOnline } = setup(async (method, path, opt) => {
    if (method === "GET") return remote;
    posts++;
    remote = { ...remote, move_count: remote.move_count + 1 };
    return remote;
  });
  o.enqueue(base, v2body);
  setOnline(true);
  o.retryAfter = Date.now() + 60000;
  await o.sync();
  assert.equal(posts, 0);
  assert.equal(o.decorate(base).pending, 1);
  await o.sync({ force: true });
  assert.equal(posts, 1);
  assert.deepEqual(o.read().queues, {});
});
test("conflicting remote move is kept and offline branch remains available for explicit resolution", async () => {
  const remote = { ...base, move_count: 1, state: { other: 1 } };
  const { o, setOnline } = setup(async (method, path) =>
    path.endsWith("/moves")
      ? [
          {
            made_by_user_id: "bob",
            new_state: { other: 1 },
            next_turn_player_indices: [1],
          },
        ]
      : remote,
  );
  o.enqueue(base, body);
  setOnline(true);
  await o.sync();
  assert.ok(o.read().conflicts.match);
  assert.equal(o.read().queues.match.length, 1);
  o.resolve("match");
  assert.equal(o.cached("/matches/match").state.other, 1);
  assert.equal(o.read().archives[0].moves.length, 1);
});
test("storage quota failure never reports a move saved", () => {
  const { o, storage } = setup();
  storage.setItem = () => {
    throw Error("quota");
  };
  assert.throws(() => o.enqueue(base, body), /not saved/);
  assert.deepEqual(o.read().queues, {});
});
test("all listed matches become available individually offline", async () => {
  const { o, setOnline } = setup(async () => [base, { ...base, id: "second" }]);
  setOnline(true);
  await o.get("/matches");
  setOnline(false);
  assert.equal((await o.get("/matches/second")).id, "second");
});
test("pending move is not applied twice when a newer server list arrives", async () => {
  const remote = {
    ...base,
    state: body.new_state,
    turn_of_player_indices: [1],
    move_count: 1,
  };
  const { o, setOnline } = setup(async () => [remote]);
  o.enqueue(base, body);
  setOnline(true);
  const list = await o.get("/matches");
  assert.equal(list[0].move_count, 1);
  assert.equal(list[0].pending, 1);
});
test("sequential offline human and computer moves synchronize in order", async () => {
  let remote = base,
    posts = 0;
  const { o, setOnline } = setup(async (method, path, opt) => {
    if (method === "GET") return remote;
    assert.equal(opt.body.expected_move_count, remote.move_count);
    posts++;
    remote = o.apply(remote, opt.body);
    return remote;
  });
  let next = o.enqueue(base, body);
  o.enqueue(next, {
    new_state: { score: 2 },
    next_turn_player_indices: [0],
    expected_move_count: 1,
  });
  setOnline(true);
  await o.sync();
  assert.equal(posts, 2);
  assert.equal(o.cached("/matches/match").move_count, 2);
  assert.deepEqual(o.read().queues, {});
});
test("an in-flight response cannot be saved into a different account", async () => {
  let release;
  const { o, setOnline, setUser } = setup(
    () => new Promise((r) => (release = r)),
  );
  setOnline(true);
  const req = o.get("/games");
  setUser({ id: "bob" });
  release([]);
  await assert.rejects(req, /Account changed/);
  assert.equal(o.cached("/games"), undefined);
});
test("409 rejection retries are bounded and preserve queued move", async () => {
  const { o, setOnline } = setup(async (method) => {
    if (method === "GET") return base;
    throw Object.assign(new Error("conflict"), { status: 409 });
  });
  o.enqueue(base, body);
  setOnline(true);
  await o.sync();
  assert.equal(o.read().queues.match.length, 1);
  assert.ok(o.read().conflicts.match);
});

test('local practice history survives storage reload and is readable offline', async () => {
  const {o,storage}=setup();
  const d=o.read();d.local['local-replay']={...base,id:'local-replay'};o.write(d);
  const match=await o.get('/matches/local-replay');o.enqueue(match,body);
  const restored=new Offline(storage,()=>{throw new Error('must stay offline');},()=>({id:'alice',password:'test'}),()=>false);
  const moves=await restored.get('/matches/local-replay/moves');
  assert.equal(moves.length,1);assert.deepEqual(moves[0].new_state,{score:1});
  assert.deepEqual(moves[0].next_turn_player_indices,[1]);
});

test('ending and resuming practice preserves the same match and moves',()=>{
  const {o}=setup();const d=o.read();d.local['local-a']={...base,id:'local-a',move_count:3,state:{score:3}};o.write(d);
  o.endPractice('local-a');let saved=o.read().local['local-a'];
  assert.equal(saved.status,'over');assert.equal(saved.state.score,3);assert.equal(saved.move_count,3);
  assert.equal(Object.keys(o.read().local).length,1);
  o.resumePractice('local-a');saved=o.read().local['local-a'];assert.equal(saved.status,'ongoing');assert.deepEqual(saved.turn_of_player_indices,[0]);
});
test('removing local practice is local-only and retains an archive',async()=>{
  const {o}=setup(()=>assert.fail('local removal must not call the server'));
  const d=o.read();d.local['local-a']={...base,id:'local-a'};o.write(d);
  await o.api('DELETE','/matches/local-a');assert.equal(o.read().local['local-a'],undefined);
  assert.equal(o.read().archivedMatches[0].match.id,'local-a');
});
test('successful match deletion archives pending work and cannot resurrect a ghost',async()=>{
  const {o,setOnline}=setup(async()=>null);setOnline(true);
  o.save('/matches',[base]);o.enqueue(base,body);
  await o.api('DELETE','/matches/match');assert.equal(o.cached('/matches/match'),undefined);
  assert.equal(o.cached('/matches').length,0);assert.equal(o.read().queues.match,undefined);
  assert.equal(o.read().archivedMatches[0].moves.length,1);
});
test('deleting an already missing owned match clears its cached ghost',async()=>{
  const {o,setOnline}=setup(async()=>{throw Object.assign(new Error('not found'),{status:404});});setOnline(true);
  o.save('/matches/match',{...base,owner_user_id:'alice'});
  await o.api('DELETE','/matches/match');assert.equal(o.cached('/matches/match'),undefined);
});
