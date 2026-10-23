const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const game={id:'game',version:1,name:'Counter',description:'Add to ten',code:'<html>counter</html>',allowed_player_counts:[1],allows_leave_mid_match:false,allows_join_mid_match:false,published:true};
function setup(request=async()=>{},catalog=async()=>[]) {
  const nodes=new Map(),storage=new Map();let user={id:'owner'},timer=0;
  function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,style:{},children:[],textContent:'',parentNode:null,
    replaceChildren(...items){this.children=items;for(const item of items)if(item&&typeof item==='object')item.parentNode=this;},
    append(...items){this.children.push(...items);for(const item of items)if(item&&typeof item==='object')item.parentNode=this;},
    focus(){},setAttribute(){},getBoundingClientRect(){return {x:100,y:100,width:420,height:500};},addEventListener(){}});return nodes.get(id);}
  const ctx=vm.createContext({console,Set,Map,AbortController,Date,JSON,structuredClone,
    document:{getElementById:node,addEventListener(){}},window:{addEventListener(){},innerWidth:1280,innerHeight:720},
    localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    setTimeout:(fn,ms)=>{if(ms===1500)Promise.resolve().then(fn);return ++timer;},clearTimeout(){},setInterval:()=>++timer,clearInterval(){},
    Auth:{current:()=>user},GameFrame:class{load(code){this.code=code;}unload(){this.code=null;}send(value){this.message=value;}},
    apiRequest:(method,url,options)=>url==='/games'?catalog():request(method,url,options),showPageError(){},stateChangedMessage:x=>x,
    el:(tag,attrs={},...children)=>({...attrs,children})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/builder.js'),'utf8').replace(/main\(\);\s*$/,''),ctx);
  const run=code=>vm.runInContext(code,ctx);
  const choose=async()=>{run('state.games='+JSON.stringify([game]));await run('select("game")');};
  return {node,run,storage,choose,setUser:value=>{user=value;}};
}
function saved(body,id='new-id',revision=1){return {...body,id,revision,base_version:0};}

test('selecting the same game preserves current work',async()=>{
  const t=setup();await t.choose();t.run('state.editor.name="New name"');await t.run('select("game")');assert.equal(t.run('state.editor.name'),'New name');
});

test('focused preview preserves the running game and emulator state',async()=>{
  const t=setup();await t.choose();
  t.run('document.body={classList:{toggle(name,on){document.focused=on;}}}');
  t.run('onTestMove({new_state:{n:1},next_turn_player_indices:[0]});focusPreview(true)');
  assert.equal(t.run('document.focused'),true);assert.equal(t.node('test-focus').textContent,'Exit preview');
  t.run('focusPreview(false)');assert.equal(t.run('document.focused'),false);
  assert.equal(t.run('state.test.state.n'),1);assert.equal(t.run('state.test.moves.length'),1);
});
test('autosave writes a working copy without publishing a version',async()=>{
  const calls=[];const t=setup(async(method,url,{body})=>{calls.push([method,url,body]);return saved(body);});
  await t.run('select("new")');t.run('state.editor={...state.editor,name:"New game",code:"new code"};scheduleAutosave()');
  assert.equal(calls.length,0);assert.equal(await t.run('flushAutosave()'),true);
  assert.equal(calls.length,1);assert.equal(calls[0][1],'/game-drafts');assert.equal(t.run('state.selectedId'),'new-id');
  assert.equal(t.run('isDirty()'),false);assert.equal(t.run('state.games.length'),0);
});
test('edits made during autosave serialize into a second write without duplicate creation',async()=>{
  const wait=deferred(),calls=[];const t=setup(async(method,url,{body})=>{calls.push([url,body]);return calls.length===1?wait.promise:saved(body,'new-id',2);});
  await t.run('select("new")');t.run('state.editor.name="First"');const first=t.run('flushAutosave()');
  t.run('state.editor.name="Second"');const second=t.run('flushAutosave()');
  wait.resolve(saved(calls[0][1]));await first;await second;
  assert.equal(calls.length,2);assert.equal(calls[1][0],'/game-drafts/new-id');assert.equal(calls[1][1].expected_revision,1);
  assert.equal(t.run('selectedDraft().name'),'Second');assert.equal(t.run('isDirty()'),false);
});
test('Publish waits for autosave and releases the saved revision once',async()=>{
  const wait=deferred(),calls=[];const t=setup(async(method,url,{body})=>{calls.push([url,body]);return url.endsWith('/publish')?{...game,id:'new-id',name:'New'}:wait.promise;});
  await t.run('select("new")');t.run('state.editor={...state.editor,name:"New",code:"code"}');
  const publish=t.run('publishVersion()');await t.run('publishVersion()');assert.equal(calls.length,1);
  wait.resolve(saved(calls[0][1]));await publish;
  assert.equal(calls.length,2);assert.equal(calls[1][0],'/game-drafts/new-id/publish');assert.equal(calls[1][1].expected_revision,1);
  assert.equal(t.run('state.games.length'),1);
});
test('autosave failure blocks publication and keeps a local recovery copy',async()=>{
  const calls=[];const t=setup(async(method,url)=>{calls.push(url);throw new Error('network unavailable');});await t.choose();
  t.run('state.editor.name="Recover me";scheduleAutosave()');await t.run('publishVersion()');
  assert.deepEqual(calls,['/game-drafts/game']);assert.match(t.node('save-status').textContent,/Autosave failed/);
  const record=JSON.parse(t.storage.get('game-platform.builder.work.v2.owner.game'));assert.equal(record.values.name,'Recover me');
});
test('a failed publish keeps the successfully autosaved game',async()=>{
  const t=setup(async(method,url,{body})=>{if(url.endsWith('/publish'))throw new Error('temporary outage');return saved(body,'game');});await t.choose();
  t.run('state.editor.name="Changed"');await t.run('publishVersion()');
  assert.equal(t.run('selectedDraft().name'),'Changed');assert.equal(t.run('state.games[0].name'),'Counter');assert.match(t.node('save-status').textContent,/Couldn't publish/);
});
test('AI updates automatically apply code and inferred counts and open the playable workspace',async()=>{
  const calls=[];const t=setup(async(method,url,{body})=>{calls.push([url,body]);if(url==='/game-drafts')return saved(body,'new-id');return {message:'Ready',name:'Solo counter',code:'<html>solo</html>',allowed_player_counts:[1]};});
  await t.run('select("new")');t.node('agent-input').value='Make a solo counter';await t.run('sendAgentMessage()');
  assert.equal(calls.find(([url])=>url==='/agent/chat')[1].player_count_mode,'auto');assert.equal(t.run('state.editor.code'),'<html>solo</html>');
  assert.equal(t.run('state.editor.allowed_player_counts[0]'),1);assert.equal(t.node('starter-view').hidden,true);assert.equal(t.node('play-view').hidden,false);
  assert.equal(t.run('state.test.players.length'),1);assert.equal(t.run('isDirty()'),true);assert.equal(t.run('state.undo.length'),1);
});
test('missing or invalid AI player counts cannot overwrite the game',async()=>{
  for(const counts of [undefined,[],[0],[2,2],['1']]){
    const t=setup(async()=>({message:'Ready',code:'bad update',allowed_player_counts:counts}));await t.choose();t.node('agent-input').value='Change it';await t.run('sendAgentMessage()');
    assert.equal(t.run('state.editor.code'),game.code);assert.match(t.node('agent-message').textContent,/valid player counts/);
  }
});
test('a rename during generation is preserved instead of applying stale code',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.choose();t.node('agent-input').value='Change it';const pending=t.run('sendAgentMessage()');
  t.run('state.editor.name="Keep my edit"');wait.resolve({message:'Done',code:'stale',allowed_player_counts:[1]});await pending;
  assert.equal(t.run('state.editor.name'),'Keep my edit');assert.equal(t.run('state.editor.code'),game.code);
});
test('late replies cannot overwrite a different selected game',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.choose();t.node('agent-input').value='Update';const pending=t.run('sendAgentMessage()');
  await t.run('select("new")');wait.resolve({message:'Late',code:'stale',allowed_player_counts:[1]});await pending;
  assert.equal(t.run('state.editor.code'),'');assert.equal(t.run('state.selectedId'),'new');
});
test('duplicate generation and publication during generation are blocked',async()=>{
  const wait=deferred();let calls=0;const t=setup(()=>{calls++;return wait.promise;});await t.choose();t.node('agent-input').value='Update';const pending=t.run('sendAgentMessage()');
  t.node('agent-input').value='Again';await t.run('sendAgentMessage()');await t.run('publishVersion()');assert.equal(calls,1);
  wait.resolve({message:'No changes'});await pending;
});
test('Undo restores code and player counts together and queues an autosave',async()=>{
  const t=setup();await t.choose();t.run('state.undo.push(editorValues());state.editor.code="new";state.editor.allowed_player_counts=[2];undoAppliedChange()');
  assert.equal(t.run('state.editor.code'),game.code);assert.deepEqual(Array.from(t.run('state.editor.allowed_player_counts')),[1]);assert.ok(t.storage.has('game-platform.builder.work.v2.owner.game'));
});
test('solo preview accepts seat zero until the game ends',async()=>{
  const t=setup();await t.choose();t.run('onTestMove({new_state:{n:1},next_turn_player_indices:[0]})');assert.equal(t.run('state.test.state.n'),1);
  t.run('onTestMove({new_state:{n:2},next_turn_player_indices:null})');assert.equal(t.run('state.test.status'),'over');
  t.run('onTestMove({new_state:{n:9},next_turn_player_indices:[0]})');assert.equal(t.run('state.test.state.n'),2);
});
test('preview rejects duplicate and nonexistent next seats',async()=>{
  const t=setup();await t.choose();for(const next of ['[0,0]','[1]','[]']){t.run('onTestMove({new_state:{bad:true},next_turn_player_indices:'+next+'})');assert.equal(t.run('state.test.moves.length'),0);}
});
test('closing the floating panel preserves a running request and reopening preserves the prompt',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.choose();t.node('agent-input').value='Update';const pending=t.run('sendAgentMessage()');
  t.run('state.aiOpen=false;render()');assert.equal(t.node('ai-panel').hidden,true);assert.equal(t.node('open-ai').hidden,false);assert.equal(t.run('state.agentRequest.cancelled'),false);
  t.node('agent-input').value='Next idea';t.run('state.aiOpen=true;render()');assert.equal(t.node('agent-input').value,'Next idea');
  wait.resolve({message:'Done'});await pending;
});
test('floating panel movement is clamped to accessible viewport coordinates',()=>{
  const t=setup();assert.equal(t.run('clampPanel(-100,-100,420,500,1280,720).x'),8);assert.equal(t.run('clampPanel(9999,9999,420,500,1280,720).x'),852);assert.equal(t.run('clampPanel(9999,9999,420,500,1280,720).y'),212);
});
test('stopping a request aborts its wait and restores the submitted prompt',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.choose();t.node('agent-input').value='Original prompt';const pending=t.run('sendAgentMessage()');
  await new Promise(setImmediate);t.run('stopAgentWaiting()');assert.equal(t.run('state.agentRequest.controller.signal.aborted'),true);wait.reject(new Error('aborted'));await pending;
  assert.equal(t.node('agent-input').value,'Original prompt');assert.equal(t.run('state.editor.code'),game.code);
});
test('preview jobs are polled without submitting another paid generation',async()=>{
  const calls=[];const t=setup(async(method,url)=>{calls.push([method,url]);return method==='POST'?{preview_job_id:'job'}:{message:'Done'};});await t.choose();t.node('agent-input').value='Update';await t.run('sendAgentMessage()');
  assert.deepEqual(calls,[['POST','/agent/chat'],['GET','/preview/jobs/job']]);
});
test('switching accounts cannot save the old editor into the new account namespace',async()=>{
  const t=setup(async()=>[]);await t.choose();t.run('state.editor.name="Old account work";rememberWork()');t.setUser({id:'other'});await t.run('onUserChanged()');
  assert.equal(t.storage.has('game-platform.builder.work.v2.other.game'),false);assert.equal(t.run('state.editor.name'),'');
});
test('an in-flight autosave cannot replace a new account workspace',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.choose();t.run('state.editor.name="Old"');const saving=t.run('flushAutosave()');
  t.setUser({id:'other'});t.run('state.session++;state.ownerId="other";state.editor=copy(EMPTY);state.selectedId="new";state.savePromise=null');wait.resolve(saved({...game,name:'Old'},'game'));await saving;
  assert.equal(t.run('state.selectedId'),'new');assert.equal(t.run('state.editor.name'),'');
});
test('recovering local edits preserves the server revision check and conversation',async()=>{
  const t=setup();await t.choose();t.run('state.editor.name="Recovered";state.messages=[{role:"user",content:"Change"}];rememberWork();state.selectedId=null');await t.run('select("game")');
  assert.equal(t.run('state.editor.name'),'Recovered');assert.equal(t.run('state.messages[0].content'),'Change');assert.equal(t.run('isDirty()'),true);
});
test('AI updates include platform join/leave rules without a settings form',async()=>{
  const t=setup(async()=>({message:'Done',code:'<html>new</html>',allowed_player_counts:[2,3],allows_join_mid_match:true,allows_leave_mid_match:true}));await t.choose();t.node('agent-input').value='Let players join and leave';await t.run('sendAgentMessage()');
  assert.equal(t.run('state.editor.allows_join_mid_match'),true);assert.equal(t.run('state.editor.allows_leave_mid_match'),true);
  t.run('undoAppliedChange()');assert.equal(t.run('state.editor.allows_join_mid_match'),false);
});
test('reload resumes the last saved workspace automatically',async()=>{
  const t=setup();await t.choose();t.run('rememberWork();state.selectedId=null;state.editor=copy(EMPTY);state.baseline=null');await t.run('resumeWorkspace()');
  assert.equal(t.run('state.selectedId'),'game');assert.equal(t.run('state.editor.code'),game.code);
});
test('a completed AI update respects a panel closed while the request was running',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.choose();t.node('agent-input').value='Update';const pending=t.run('sendAgentMessage()');
  t.run('state.aiOpen=false;render()');wait.resolve({message:'Updated',code:'<html>new</html>',allowed_player_counts:[1]});await pending;
  assert.equal(t.run('state.editor.code'),'<html>new</html>');assert.equal(t.node('ai-panel').hidden,true);assert.equal(t.node('open-ai').hidden,false);
});

test('an existing public game becomes an autosaved independent copy without an AI request',async()=>{
  const calls=[],source=JSON.stringify(game);
  const t=setup(async(method,url,{body})=>{calls.push([method,url,body]);return saved(body,'copy-id');},async()=>[game]);
  await t.run('select("new")');assert.equal(t.node('starter-game').value,'game');t.run('useStarter()');
  assert.equal(t.run('state.editor.name'),'Counter (copy)');assert.equal(t.run('state.editor.code'),game.code);
  assert.equal(t.node('starter-view').hidden,true);assert.equal(t.run('state.test.players.length'),1);
  await t.run('flushAutosave()');assert.equal(calls.length,1);assert.equal(calls[0][1],'/game-drafts');
  assert.equal(t.run('state.selectedId'),'copy-id');assert.equal(t.run('state.games.length'),0);assert.equal(JSON.stringify(game),source);
});

test('the starting-game catalog shows all games and filters names and descriptions',async()=>{
  const catalog=Array.from({length:41},(_,i)=>({...game,id:'g'+i,name:'Game '+i,description:i===40?'Special puzzle':'Counter'}));
  const t=setup(undefined,async()=>catalog);await t.run('select("new")');assert.equal(t.node('starter-game').children.length,41);
  t.node('starter-search').value='SPECIAL';t.run('renderStarters()');assert.equal(t.node('starter-game').children.length,1);assert.equal(t.node('starter-game').value,'g40');
  t.node('starter-search').value='absent';t.run('renderStarters()');assert.equal(t.node('use-starter').disabled,true);
});

test('catalog failure allows retry without overwriting a game',async()=>{
  let attempts=0;const t=setup(undefined,async()=>{if(++attempts===1)throw new Error('offline');return [game];});
  await t.run('select("new")');assert.match(t.node('starter-message').textContent,/offline/);assert.equal(t.node('load-starters').disabled,false);
  await t.run('loadStarters()');assert.equal(t.node('use-starter').disabled,false);assert.equal(t.run('state.editor.code'),'');
});

test('a late catalog cannot replace a selected game',async()=>{
  const wait=deferred(),t=setup(undefined,()=>wait.promise);const loading=t.run('select("new")');await t.choose();
  wait.resolve([game]);await loading;t.run('useStarter()');assert.equal(t.run('state.selectedId'),'game');assert.equal(t.run('state.editor.name'),'Counter');
});

test('a late catalog response is ignored after switching accounts',async()=>{
  const wait=deferred(),t=setup(async()=>[],()=>wait.promise);const loading=t.run('select("new")');
  t.setUser({id:'other'});await t.run('onUserChanged()');wait.resolve([game]);await loading;
  assert.equal(t.run('state.starters.length'),0);assert.equal(t.run('state.editor.code'),'');assert.equal(t.run('state.starterLoading'),false);
});


test('emulator pauses, single steps, undoes and redoes a terminal move',async()=>{
  const t=setup();await t.choose();t.run('pauseTest()');
  assert.equal(t.run('state.test.awaiting'),false);
  t.run('onTestMove({new_state:99,next_turn_player_indices:null})');
  assert.equal(t.run('state.test.moves.length'),0);
  t.run('stepTest();onTestMove({new_state:{score:1},next_turn_player_indices:null})');
  assert.equal(t.run('state.test.paused'),true);assert.equal(t.run('state.test.status'),'over');
  t.run('undoTestMove()');assert.equal(t.run('state.test.state'),null);
  assert.equal(t.run('state.test.awaiting'),false);
  t.run('redoTestMove()');assert.equal(t.run('state.test.state.score'),1);
  assert.equal(t.run('state.test.status'),'over');
  assert.match(t.node('test-state').textContent,/score/);
});
test('a new emulator move clears the abandoned redo branch',async()=>{
  const t=setup();await t.choose();
  t.run('onTestMove({new_state:1,next_turn_player_indices:[0]});undoTestMove();stepTest();onTestMove({new_state:2,next_turn_player_indices:[0]})');
  assert.equal(t.run('state.test.redo.length'),0);assert.equal(t.run('state.test.state'),2);
  assert.equal(t.run('state.test.paused'),true);
});


test('stopping while a new draft is being created restores the prompt',async()=>{
  const wait=deferred(),t=setup(()=>wait.promise);await t.run('select("new")');
  t.node('agent-input').value='Keep this prompt';const pending=t.run('sendAgentMessage()');
  t.run('stopAgentWaiting()');wait.resolve(saved({name:'Draft',code:'',allowed_player_counts:[1]},'new-id'));await pending;
  assert.equal(t.node('agent-input').value,'Keep this prompt');assert.equal(t.run('state.agentSending'),false);
});
