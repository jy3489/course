const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const games=path.join(__dirname,'../../games/audit-batch1');

function setup(name){
  const nodes=new Map(),sent=[];let receive;
  const element=()=>({children:[],style:{},textContent:'',innerHTML:'',disabled:false,
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
    querySelectorAll(){return [];},addEventListener(type,fn){this['on'+type]=fn;}});
  const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},createElement:element};
  const parent={postMessage:message=>sent.push(message)};
  const sandbox={document,parent,addEventListener:(type,fn)=>{if(type==='message')receive=fn;}};
  sandbox.window=sandbox;
  const code=fs.readFileSync(path.join(games,name+'.html'),'utf8').match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(code,sandbox);
  return {nodes,sent,send:message=>receive({source:parent,data:message}),otherSend:message=>receive({source:{},data:message})};
}
const message={type:'state_changed',status:'ongoing',move_count:0,my_player_index:0,acting_for_player_index:0,
  players:[{name:'You'},{name:'Opponent'}],view:{hand:[42,9,7],playable:[9,7],top:22,suit:0,counts:[3,3],deckCount:40,turnIdx:0,done:false}};

test('Crazy Eights disables illegal cards and guards their handlers',()=>{
  const t=setup('crazy-eights');t.send(message);
  const [illegal,legal]=t.nodes.get('hand').children;
  assert.equal(illegal.disabled,true);illegal.onclick();assert.equal(t.sent.length,0);
  assert.equal(legal.disabled,false);legal.onclick();assert.equal(t.sent.length,1);assert.equal(t.sent[0].move.card,9);
});
test('Crazy Eights blocks duplicate submissions until an authoritative state update',()=>{
  const t=setup('crazy-eights');t.send(message);
  const legal=t.nodes.get('hand').children[1],draw=t.nodes.get('actions').children[0];
  legal.onclick();legal.onclick();draw.onclick();
  assert.equal(t.sent.length,1);assert.equal(t.nodes.get('status').textContent,'Saving move…');
  // A rejected move is followed by the portal's refreshed, unchanged state.
  t.send({...message,error:'Move rejected'});
  assert.match(t.nodes.get('status').textContent,/your turn/);
  t.nodes.get('actions').children[0].onclick();assert.equal(t.sent.length,2);assert.equal(t.sent[1].move,'draw');
});
test('Crazy Eights ignores foreign updates and clears pending state after move advancement',()=>{
  const t=setup('crazy-eights');t.send(message);t.nodes.get('actions').children[0].onclick();
  t.otherSend(message);t.nodes.get('actions').children[0].onclick();assert.equal(t.sent.length,1);
  t.send({...message,move_count:2});t.nodes.get('actions').children[0].onclick();assert.equal(t.sent.length,2);
});
test('Crazy Eights wild card requires one suit selection and sends only once',()=>{
  const t=setup('crazy-eights');t.send(message);t.nodes.get('hand').children[2].onclick();assert.equal(t.sent.length,0);
  const suit=t.nodes.get('actions').children[2];suit.onclick();suit.onclick();
  assert.equal(t.sent.length,1);assert.equal(t.sent[0].move.card,7);assert.equal(t.sent[0].move.chosenSuit,1);
});
test('Crazy Eights spectators and finished games cannot submit',()=>{
  const t=setup('crazy-eights');t.send({...message,acting_for_player_index:null});
  for(const card of t.nodes.get('hand').children){assert.equal(card.disabled,true);card.onclick();}
  t.send({...message,status:'over',view:{...message.view,done:true,winner:0}});
  for(const card of t.nodes.get('hand').children){assert.equal(card.disabled,true);card.onclick();}
  assert.equal(t.sent.length,0);
});
test('Beat the Landlord client parses and renders actual server views',()=>{
  const sandbox={InvalidMove:class InvalidMove extends Error{}};vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(games,'beat-the-landlord.server.js'),'utf8')+'\nglobalThis.auditGame=game;',sandbox);
  const ctx={players:[0,1,2].map(i=>({id:'p'+i,kind:'human',display_name:'Player'+i})),pot:60,move_count:0,random:()=>.5};
  const game=sandbox.auditGame,initial=game.newMatch(ctx),view=game.viewFor(initial.state,'p0',ctx);
  const t=setup('beat-the-landlord');t.send({...message,players:ctx.players.map(p=>({name:p.display_name})),view});
  assert.match(t.nodes.get('t').innerHTML,/Bidding/);assert.match(t.nodes.get('t').innerHTML,/Player0/);
});
