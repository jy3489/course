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
  const ctx = vm.createContext({console, URLSearchParams, Set, setTimeout: fn=>fn(),
    document:{getElementById:node}, window:{addEventListener(){}},
    Auth:{current:()=>({id:'user'})},
    GameFrame:class {load(){} unload(){} send(){}},
    apiRequest:request, formatTime:()=>'', el:()=>node(Symbol()),
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
