"use strict";
// Same deterministic v2 contract as game_logic.py. A parity test guards this copy.
const PRACTICE_PRELUDE = String.raw`
class InvalidMove extends Error {
  constructor(message) {
    super(message);
    this.name = "InvalidMove";
  }
}

// Deterministic randomness (mulberry32). The generator is reseeded by
// __invoke on every call, so Math.random and ctx.random draw from the
// same reproducible sequence for that call and no call can leak RNG
// state into the next one.
let __rngState = 0;
function __seedRandom(seed) {
  __rngState = seed >>> 0;
}
function __random() {
  __rngState = (__rngState + 0x6D2B79F5) | 0;
  let t = __rngState;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t = (t + Math.imul(t ^ (t >>> 7), t | 61)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
Math.random = __random;

// A frozen clock: a no-argument Date is the move's time (set by __invoke
// on every call) and Date.now() agrees with it, so a replay sees exactly
// the same time as the original call.
const __RealDate = Date;
let __frozenMillis = 0;
class __FrozenDate extends __RealDate {
  constructor(...args) {
    if (args.length === 0) {
      super(__frozenMillis);
    } else {
      super(...args);
    }
  }
  static now() {
    return __frozenMillis;
  }
  static parse(text) {
    return __RealDate.parse(text);
  }
  static UTC(...args) {
    return __RealDate.UTC(...args);
  }
}
Date = __FrozenDate;

// Monte-Carlo win estimate, exposed to game code as
// ctx.estimateWinChance(state, playerId, turn?): plays the game out from
// the given state with randomMove (or computerMove when the game defines
// no randomMove), up to __ESTIMATE_WALKS times, and returns the fraction
// of walks the player wins — a walk is won when the player's final payout
// is the largest (a tie for largest counts as an equal share of a win).
// [turn], when given, is who may move first; without it, the first mover
// is discovered by trying each player until one can move. The walk and
// move caps keep the estimate bounded (about a second for typical
// games), and because every random draw comes from the call's seeded
// generator the estimate is deterministic for a given call. A walk that
// errors or does not finish within the move cap counts as played but
// not won.
const __ESTIMATE_WALKS = 100;
const __ESTIMATE_MAX_MOVES = 200;
function __estimateWalk(ctx, moveFor, state, firstTurn) {
  // One walk: the final payouts object, or null when the walk fails or
  // the game does not end within the move cap.
  let current = JSON.parse(JSON.stringify(state === undefined ? null : state));
  let turn = firstTurn ? firstTurn.slice() : null;
  for (let step = 0; step < __ESTIMATE_MAX_MOVES; step++) {
    if (turn === null) {
      // First step, and the caller did not say whose turn it is: try
      // each player until one can actually move.
      let moved = false;
      for (const candidate of ctx.players) {
        try {
          const move = moveFor.call(game, current, candidate.id, ctx);
          const result = game.applyMove(current, candidate.id, move, ctx);
          current = result.state;
          if (result.turn === null) return result.payouts || null;
          turn = result.turn;
          moved = true;
          break;
        } catch (e) { /* that player cannot move here; try the next */ }
      }
      if (!moved) return null;
      continue;
    }
    if (turn.length === 0) return null;
    const mover = turn[Math.floor(__random() * turn.length)];
    let result;
    try {
      const move = moveFor.call(game, current, mover, ctx);
      result = game.applyMove(current, mover, move, ctx);
    } catch (e) {
      return null;
    }
    current = result.state;
    if (result.turn === null) return result.payouts || null;
    turn = result.turn;
  }
  return null;
}
function __estimateWinChance(ctx, state, playerId, firstTurn) {
  const moveFor = typeof game.randomMove === "function" ? game.randomMove
    : typeof game.computerMove === "function" ? game.computerMove : null;
  if (moveFor === null || typeof game.applyMove !== "function") return 0;
  let score = 0;
  for (let walk = 0; walk < __ESTIMATE_WALKS; walk++) {
    const payouts = __estimateWalk(ctx, moveFor, state, firstTurn);
    if (payouts === null) continue;
    const mine = payouts[playerId] || 0;
    let best = 0;
    let tied = 0;
    for (const id of Object.keys(payouts)) {
      if (payouts[id] > best) { best = payouts[id]; tied = 1; }
      else if (payouts[id] === best) { tied++; }
    }
    if (mine > 0 && mine === best) score += 1 / tied;
  }
  return score / __ESTIMATE_WALKS;
}

// Calls one game function and reports the outcome as a JSON envelope:
//   {"ok": true, "result": <the function's JSON result>}
//   {"ok": false, "kind": "invalid_move", "message": ...}
//   {"ok": false, "kind": "error", "message": ..., "stack": ...}
// argsJson is a JSON array of the function's arguments *without* ctx;
// ctxJson is the ctx object as JSON, to which this helper adds random().
function __invoke(name, argsJson, ctxJson, seed, frozenMillis) {
  __seedRandom(seed);
  __frozenMillis = frozenMillis;
  try {
    const fn = (typeof game !== "undefined" && game !== null) ? game[name] : undefined;
    if (typeof fn !== "function") {
      return JSON.stringify({ok: false, kind: "error",
        message: "game." + name + " is not defined", stack: ""});
    }
    const ctx = JSON.parse(ctxJson);
    ctx.random = __random;
    ctx.estimateWinChance = function (state, playerId, turn) {
      return __estimateWinChance(ctx, state, playerId, turn);
    };
    const args = JSON.parse(argsJson);
    args.push(ctx);
    const result = fn.apply(game, args);
    const text = JSON.stringify(result === undefined ? null : result);
    if (text === undefined) {
      return JSON.stringify({ok: false, kind: "error",
        message: "game." + name + " returned a value that is not JSON", stack: ""});
    }
    return JSON.stringify({ok: true, result: JSON.parse(text)});
  } catch (e) {
    if (e instanceof InvalidMove) {
      return JSON.stringify({ok: false, kind: "invalid_move",
        message: String(e.message || e)});
    }
    return JSON.stringify({ok: false, kind: "error",
      message: String((e && e.message) || e),
      stack: String((e && e.stack) || "")});
  }
}
`;

// Runs inside a Worker in an opaque-origin sandbox. Never evaluate game code in
// the portal: it must not have access to account credentials, storage or network.
function practiceWorker(prelude) {
  self.onmessage = ({data}) => {
    try {
      const invoke = new Function(prelude + "\n" + data.code + "\nreturn __invoke;")();
      self.postMessage(JSON.parse(invoke(data.name, JSON.stringify(data.args),
        JSON.stringify(data.ctx), data.seed, data.now)));
    } catch (error) {
      self.postMessage({ok:false, message:String(error.message || error)});
    }
  };
}

class PortalPractice {
  static call(game, name, args, match) {
    const payload = {code:game.server_code, name, args,
      ctx:{players:match.players.map(p=>({id:p.id,kind:p.kind,
        display_name:p.name || `Player ${p.player_index+1}`,picture_url:"",max_loss:0})),
        pot:0,move_count:match.move_count},
      seed:((match.practiceSeed || 0)+match.move_count)>>>0,
      now:Date.parse(match.created_at)};
    return new Promise((resolve,reject)=>{
      const frame=document.createElement("iframe");
      frame.hidden=true;
      frame.setAttribute("sandbox","allow-scripts");
      frame.setAttribute("title","Local practice rules");
      const workerSource=`(${practiceWorker.toString()})(${JSON.stringify(PRACTICE_PRELUDE)})`;
      // JSON encoding plus '<' escaping prevents game source from ending srcdoc's script.
      const script=`const worker=new Worker(URL.createObjectURL(new Blob([${JSON.stringify(workerSource)}],{type:"text/javascript"})));window.onmessage=e=>{if(e.source===parent)worker.postMessage(e.data);};worker.onmessage=e=>parent.postMessage(e.data,"*");worker.onerror=()=>parent.postMessage({ok:false,message:"Local game failed to run"},"*");`;
      frame.srcdoc=`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; worker-src blob:; connect-src 'none'"><script>${script.replace(/</g,"\u003c")}<`+"/script>";
      let timer;
      const finish=(error,result)=>{
        clearTimeout(timer);window.removeEventListener("message",receive);frame.remove();
        error ? reject(error) : resolve(result);
      };
      const receive=event=>{
        if(event.source!==frame.contentWindow)return;
        const reply=event.data;
        if(!reply || typeof reply.ok!=="boolean")return;
        finish(reply.ok ? null : new Error(reply.message || "Local game failed"),reply.result);
      };
      window.addEventListener("message",receive);
      frame.onload=()=>frame.contentWindow.postMessage(payload,"*");
      // Terminating the iframe also terminates a Worker stuck in game code.
      timer=setTimeout(()=>finish(new Error("Local game took too long. Your last saved position is unchanged.")),3000);
      document.body.append(frame);
    });
  }
}
if(typeof module!=="undefined")module.exports={PortalPractice,PRACTICE_PRELUDE,practiceWorker};
