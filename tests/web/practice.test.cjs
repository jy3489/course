const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {webcrypto}=require('node:crypto');
const {PRACTICE_PRELUDE,practiceWorker}=require('../../static/portal-practice.js');
const root=path.join(__dirname,'../..');
function setup(gameName='tictactoe',mode='computer') {
 const writes=new Map();let requests=0;
 const ctx=vm.createContext({console,crypto:webcrypto,Uint32Array,
  PortalPractice:{async call(game,name,args,match){
   let reply;
   const self={postMessage:value=>{reply=value;}};
   const context=vm.createContext({self});
   vm.runInContext(`(${practiceWorker.toString()})(${JSON.stringify(PRACTICE_PRELUDE)})`,context);
   self.onmessage({data:{code:game.server_code,name,args,
    ctx:{players:match.players.map(p=>({...p,display_name:p.name||'Player',max_loss:0})),pot:0,move_count:match.move_count},
    seed:(match.practiceSeed+match.move_count)>>>0,now:Date.parse(match.created_at)}});
   if(!reply.ok)throw new Error(reply.message);
   return JSON.parse(JSON.stringify(reply.result));
  }}});
 vm.runInContext(fs.readFileSync(path.join(root,'static/portal-offline.js'),'utf8')+';globalThis.Offline=PortalOffline;',ctx);
 const storage={getItem:k=>writes.get(k),setItem:(k,v)=>writes.set(k,v)};
 const offline=new ctx.Offline(storage,()=>{requests++;throw new Error('network forbidden');},()=>({id:'alice'}),()=>false);
 const game={id:gameName,version:2,allowed_player_counts:gameName==='poker'?[2,3,4,5,6]:[2],
  code:'<html>v2 client</html>',server_code:fs.readFileSync(path.join(root,`games/${gameName}.server.js`),'utf8')};
 return {offline,game,mode,storage,get requests(){return requests;}};
}
test('v2 computer practice initializes, rejects illegal moves atomically, and survives offline reload',async()=>{
 const t=setup();let m=await t.offline.practice(t.game);
 assert.equal(m.state.board.length,9);assert.equal(m.token_pot,0);
 m=await t.offline.practiceMove(m,{cell:0});
 assert.equal(m.move_count,2);assert.equal(m.state.board.filter(Boolean).length,2);
 assert.equal(m.turn_of_player_indices[0],0);
 const saved=JSON.stringify(t.offline.read());
 await assert.rejects(t.offline.practiceMove(m,{cell:0}),/taken/);
 assert.equal(JSON.stringify(t.offline.read()),saved);
 const restored=new t.offline.constructor(t.storage,()=>{throw new Error('network');},()=>({id:'alice'}),()=>false);
 m=await restored.get(`/matches/${m.id}`);
 const cell=m.state.board.findIndex(x=>!x);
 m=await restored.practiceMove(m,{cell});
 const history=await restored.get(`/matches/${m.id}/history`);
 assert.equal(history.initial_state.board.filter(Boolean).length,0);
 assert.equal(history.moves.length,m.move_count);
 assert.equal(t.requests,0);
});
test('pass-and-play v2 reaches a win with no computer or network moves',async()=>{
 const t=setup();let m=await t.offline.practice(t.game,'pass');
 for(const cell of [0,3,1,4,2])m=await t.offline.practiceMove(m,{cell});
 assert.equal(m.status,'over');assert.equal(m.state.winner,0);assert.equal(m.move_count,5);
 assert.equal(m.turn_of_player_indices,null);assert.equal(t.requests,0);
 await assert.rejects(t.offline.practiceMove(m,{cell:8}),/changed/);
});
test('poker practice keeps full rules state separate from the human view and runs its computer',async()=>{
 const t=setup('poker');let m=await t.offline.practice(t.game);
 assert.notDeepEqual(m.logicState,m.state);
 assert.equal(m.players[1].kind,'computer');assert.equal(m.token_pot,0);
 // Folding is a legal human action; all subsequent computer decisions stay local.
 m=await t.offline.practiceMove(m,{type:'fold'});
 assert.ok(m.move_count>=1);assert.equal(t.requests,0);
});
test('online match ids are never accepted by the local rules runner',async()=>{
 const t=setup();await assert.rejects(t.offline.practiceMove({id:'online-match'},{}),/Only device practice/);
});
test('v1 practice still starts with browser-owned state',async()=>{
 const t=setup();delete t.game.server_code;
 const m=await t.offline.practice(t.game);assert.equal(m.state,null);assert.equal(m.move_count,0);
});
