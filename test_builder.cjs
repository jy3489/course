const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
function setup(request = async () => {}) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'', checked:false, disabled:false, hidden:false,
      textContent:'', children:[], replaceChildren(...items){this.children=items;},
      append(...items){this.children.push(...items);}, prepend(...items){this.children.unshift(...items);}, querySelectorAll(){return [];},
      setAttribute(){}, addEventListener(){}});
    return nodes.get(id);
  };
  const ctx = vm.createContext({console, URLSearchParams, Set, AbortController, setInterval, clearInterval, setTimeout: fn=>fn(),
    document:{getElementById:node}, window:{addEventListener(){}},
    Auth:{current:()=>({id:'user'})},
    GameFrame:class {load(code){this.lastCode=code;} unload(){} send(){}},
    apiRequest:request, formatTime:()=>'', el:(tag,attrs={})=>Object.assign(attrs.id?node(attrs.id):node(Symbol()),attrs),
    stateChangedMessage: x=>x});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'static/builder.js'),'utf8').replace(/main\(\);\s*$/, ''),ctx);
  const run = code=>vm.runInContext(code,ctx);
  run('select("new");');
  node('name').value='Game';
  node('count-2').checked=true;
  return {node,run};
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
test('selecting the current game preserves unsaved edits',()=>{
  const {node,run}=setup(); node('code').value='unsaved';run('select("new")');assert.equal(node('code').value,'unsaved');
});
test('duplicate create requests are blocked and edits during save survive',async()=>{
  const wait=deferred();let calls=0;
  const {node,run}=setup(()=>{calls++;return wait.promise;});
  const first=run('save()');await run('save()');assert.equal(calls,1);assert.equal(node('save').disabled,true);
  node('code').value='newer';
  wait.resolve({id:'saved',version:1,name:'Game',description:'',code:'old',allowed_player_counts:[2]});
  await first;assert.equal(node('code').value,'newer');assert.equal(run('isDirty()'),true);
  assert.match(node('agent-message').textContent,/Published version 1/);
  assert.equal(node('play-game').href,'/portal#game=saved');
});
test('pending proposals prevent publishing older editor code',async()=>{
  let calls=0;const {node,run}=setup(async()=>{calls++;});
  run('agentHistory().pending={code:"proposal"}');await run('save()');
  assert.equal(calls,0);assert.match(node('agent-message').textContent,/apply or discard/);
});
test('late agent replies are ignored after switching conversations',async()=>{
  const wait=deferred();const {node,run}=setup(()=>wait.promise);
  node('agent-input').value='build chess';const first=run('sendAgentMessage()');
  run('state.baseline=JSON.stringify(editorValues()); select("other")');
  wait.resolve({message:'old reply',code:'<html>old</html>'});await first;
  assert.equal(node('agent-log').children.length,0);assert.equal(run('state.agent'),null);
  assert.equal(node('agent-send').disabled,false);
});
test('keyboard send cannot start concurrent agent requests',async()=>{
  const wait=deferred();let calls=0;const {node,run}=setup(()=>{calls++;return wait.promise;});
  node('agent-input').value='first';const first=run('sendAgentMessage()');
  node('agent-input').value='second';await run('sendAgentMessage()');assert.equal(calls,1);
  wait.resolve({message:'done'});await first;
});
test('local tests reject duplicate and out-of-range next seats',()=>{
  const {run}=setup();
  run('state.test={players:[{name:"A",kind:"human"},{name:"B",kind:"human"}],moves:[],state:null,turn:[0],status:"ongoing",awaiting:true}');
  run('onTestMove({new_state:{},next_turn_player_indices:[1,1]})');assert.equal(run('state.test.moves.length'),0);
  run('onTestMove({new_state:{},next_turn_player_indices:[2]})');assert.equal(run('state.test.moves.length'),0);
  run('onTestMove({new_state:{},next_turn_player_indices:[1]})');assert.equal(run('state.test.moves.length'),1);
});

test('publish failures are visible in both tabs and unlock retry',async()=>{
  const {node,run}=setup(async()=>{throw new Error('offline');});
  await run('save()');
  assert.match(node('agent-message').textContent,/offline/);
  assert.equal(node('edit-message').textContent,node('agent-message').textContent);
  assert.equal(node('save').disabled,false);
});
test('late agent failures cannot overwrite a new conversation input',async()=>{
  const wait=deferred();const {node,run}=setup(()=>wait.promise);
  node('agent-input').value='old prompt';const first=run('sendAgentMessage()');
  run('resetAgent()');node('agent-input').value='new prompt';
  wait.reject(new Error('old failure'));await first;
  assert.equal(node('agent-input').value,'new prompt');
  assert.equal(node('agent-message').textContent,'');
});

test('leaving warns for drafts and proposals but not clean saved games',()=>{
  const {run}=setup();
  assert.equal(run('let warned=false; protectUnsavedChanges({preventDefault(){warned=true;}}); warned'),true);
  assert.equal(run('state.baseline=JSON.stringify(editorValues()); warned=false; protectUnsavedChanges({preventDefault(){warned=true;}}); warned'),false);
  assert.equal(run('agentHistory().pending={code:"draft"}; warned=false; protectUnsavedChanges({preventDefault(){warned=true;}}); warned'),true);
});

test('connected preview polls its job without repeating the paid request',async()=>{
  const calls=[];
  const {node,run}=setup(async(method,path)=>{
    calls.push([method,path]);
    return method==='POST' ? {preview_job_id:'job-1'} : {message:'Muse finished',code:'<html>game</html>'};
  });
  node('agent-input').value='backgammon';await run('sendAgentMessage()');
  assert.deepEqual(calls,[['POST','/agent/chat'],['GET','/preview/jobs/job-1']]);
  assert.equal(run('state.agent.pending.code'),'<html>game</html>');
  assert.equal(node('agent-send').disabled,false);
});

test('a one-seat test keeps seat zero until the solo game ends',()=>{
  const {node,run}=setup();
  node('count-1').checked=true;node('count-2').checked=false;
  run('renderTestSetup()');
  assert.equal(node('test-mode-solo').disabled,false);
  assert.equal(node('test-mode-computer').disabled,true);
  run('state.test={players:[{name:"Solo",kind:"human"}],moves:[],state:null,turn:[0],status:"ongoing",awaiting:true}');
  run('onTestMove({new_state:{score:1},next_turn_player_indices:[0]})');
  assert.equal(run('state.test.status'),'ongoing');
  run('onTestMove({new_state:{score:2},next_turn_player_indices:null})');
  assert.equal(run('state.test.status'),'over');
});

test('applied AI changes are drafts, open the editing workspace, and can be undone',()=>{
  const {node,run}=setup();node('code').value='original';
  run('state.baseline=JSON.stringify(editorValues()); agentHistory().pending={code:"updated",name:"Renamed game",base:JSON.stringify(editorValues())}; applyAgentProposal()');
  assert.equal(node('code').value,'updated');assert.equal(node('name').value,'Renamed game');
  assert.equal(run('state.activeTab'),'edit');assert.equal(run('isDirty()'),true);
  run('undoAppliedChange()');assert.equal(node('code').value,'original');assert.equal(node('name').value,'Game');
  assert.equal(run('isDirty()'),false);
});

test('follow-up prompts send the latest applied game code and conversation',async()=>{
  const bodies=[];
  const {node,run}=setup(async(method,path,opts)=>{
    bodies.push(opts.body);
    return {message:'Changed',code:bodies.length===1?'first draft':'second draft'};
  });
  node('code').value='initial';node('agent-input').value='Change the win rule';
  await run('sendAgentMessage()');run('applyAgentProposal()');
  node('agent-input').value='Now add a score counter';await run('sendAgentMessage()');
  assert.equal(bodies[1].code,'first draft');assert.equal(bodies[1].messages.length,3);
  assert.equal(bodies[1].messages[2].content,'Now add a score counter');
  assert.equal(node('code').value,'first draft'); // review is required before replacing it
});

test('an outdated proposal cannot overwrite edits made during generation',async()=>{
  const wait=deferred();const {node,run}=setup(()=>wait.promise);
  node('agent-input').value='change rules';const request=run('sendAgentMessage()');
  node('code').value='my newer edit';wait.resolve({message:'done',code:'outdated proposal'});await request;
  run('applyAgentProposal()');assert.equal(node('code').value,'my newer edit');
  assert.match(node('agent-message').textContent,/draft changed/);
});

test('loading a published version creates an undoable unpublished draft',async()=>{
  const paths=[];
  const {node,run}=setup(async(method,path)=>{paths.push(path);return {name:'Game',description:'',code:'version one',allowed_player_counts:[2]};});
  run('state.games=[{id:"saved",version:2}];state.selectedId="saved";state.baseline=JSON.stringify(editorValues())');
  await run('restorePublishedVersion(1)');
  assert.equal(paths[0],'/games/saved/versions/1');assert.equal(node('code').value,'version one');
  assert.equal(run('isDirty()'),true);assert.equal(run('state.draftUndo.length'),1);
});

test('switching between test and edit preserves the running test snapshot',()=>{
  const {node,run}=setup();node('code').value='edited after test began';
  run('state.test={code:"running code",players:[{kind:"human",name:"A"}],moves:[],turn:[0],state:{score:4},status:"ongoing"};showTab("edit")');
  assert.equal(run('frame.lastCode'),'running code');assert.equal(run('state.test.state.score'),4);
});

test('stopping a request preserves the prompt and ignores its eventual reply',async()=>{
  const wait=deferred();const {node,run}=setup(()=>wait.promise);
  node('agent-input').value='implement backgammon';const first=run('sendAgentMessage()');
  assert.equal(node('agent-stop').hidden,false);run('stopAgentWaiting()');
  wait.resolve({message:'late',code:'late code'});await first;
  assert.equal(node('agent-input').value,'implement backgammon');
  assert.equal(run('state.agent.pending'),null);assert.equal(run('state.agentSending'),false);
  assert.match(node('agent-message').textContent,/Stopped waiting/);
});

test('starting from a catalog game creates an attributed unpublished copy',async()=>{
  let calls=0;const {node,run}=setup(async()=>{calls++;return [{id:'bg',version:3,name:'Backgammon',description:'Board game',code:'published code',allowed_player_counts:[2]}];});
  await run('loadStarters()');node('starter-game').value='bg';run('useStarter()');
  assert.equal(calls,1);assert.equal(node('code').value,'published code');
  assert.equal(node('name').value,'Backgammon (copy)');assert.match(node('description').value,/Based on Backgammon v3/);
  assert.equal(run('state.selectedId'),'new');assert.equal(run('state.activeTab'),'edit');
  assert.equal(run('isDirty()'),true);
});

test('Muse solo counts apply with code, undo with code, and publish as one player',async()=>{
  const calls=[];
  const {node,run}=setup(async(method,path,opts)=>{
    calls.push([method,path,opts.body]);
    if(path==='/agent/chat') return {message:'Solo Hanoi',code:'<html>Hanoi</html>',allowed_player_counts:[1]};
    return {...opts.body,id:'solo',version:1};
  });
  node('agent-input').value='Tower of Hanoi'; await run('sendAgentMessage()');
  assert.equal(calls[0][2].player_count_mode,'auto');
  assert.match(node('agent-proposed-players').textContent,/1 \(solo\)/);
  assert.equal(node('count-2').checked,true); // review before applying metadata
  run('applyAgentProposal()');
  assert.equal(node('count-1').checked,true);assert.equal(node('count-2').checked,false);
  await run('save()');
  assert.deepEqual(Array.from(calls[1][2].allowed_player_counts),[1]);
});

test('undoing a solo proposal restores the previous player counts too',async()=>{
  const {node,run}=setup(async()=>({message:'Solo',code:'<html>solo</html>',allowed_player_counts:[1]}));
  node('agent-input').value='Make it solo';await run('sendAgentMessage()');run('applyAgentProposal();undoAppliedChange()');
  assert.equal(node('count-1').checked,false);assert.equal(node('count-2').checked,true);
});

test('visible solo override configures one seat and sends a manual constraint',async()=>{
  let body;
  const {node,run}=setup(async(method,path,opts)=>{body=opts.body;return {message:'done'};});
  node('generation-player-mode').value='solo';run('changeGenerationMode()');
  assert.equal(node('count-1').checked,true);assert.equal(node('count-2').checked,false);
  node('agent-input').value='Hanoi';await run('sendAgentMessage()');
  assert.equal(body.player_count_mode,'manual');assert.deepEqual(Array.from(body.allowed_player_counts),[1]);
});

test('changing player mode during generation invalidates its proposal',async()=>{
  const wait=deferred();const {node,run}=setup(()=>wait.promise);
  node('agent-input').value='Hanoi';const request=run('sendAgentMessage()');
  node('generation-player-mode').value='custom';
  wait.resolve({message:'Solo',code:'<html>solo</html>',allowed_player_counts:[1]});await request;
  run('applyAgentProposal()');assert.notEqual(node('code').value,'<html>solo</html>');
  assert.match(node('agent-message').textContent,/player mode changed/);
});
