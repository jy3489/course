// Speed matches in the portal: the countdown, the time-ran-out messages, and the
// "time per move" choice when creating a match.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

const read=name=>fs.readFileSync(path.join(__dirname,'../../static',name),'utf8');
const I18n={t:(key,params,fallback)=>fallback??key,locale:()=>'en',revision:()=>0};

function loadSpeed() {
  const window={};
  vm.runInContext(read('portal-speed.js'),vm.createContext({window,I18n,Date,Math,Number,String,Set}));
  return window.PortalSpeed;
}

const T0=Date.parse('2026-01-01T00:00:00Z');
const names={ann:'Ann',bob:'Bob'};
const nameOf=id=>names[id]??id;
function speedMatch(overrides={}) {
  return {id:'m',status:'ongoing',turn_seconds:60,turn_of_player_indices:[0],
    turn_deadline:'2026-01-01T00:01:00Z',server_time:'2026-01-01T00:00:00Z',timeouts:[],
    players:[{player_index:0,kind:'human',user_id:'ann'},{player_index:1,kind:'human',user_id:'bob'}],...overrides};
}

test('the countdown uses the server clock, even when the phone clock is wrong',()=>{
  const speed=loadSpeed();
  const phoneAhead=T0+5*60*1000; // this phone thinks it's 5 minutes later
  speed.observe(speedMatch(),phoneAhead);
  assert.equal(speed.secondsLeft(speedMatch(),phoneAhead),60);
  assert.equal(speed.secondsLeft(speedMatch(),phoneAhead+18_000),42);
  // A cached (offline) copy repeats the old server_time and doesn't move the estimate.
  speed.observe(speedMatch(),phoneAhead+30_000);
  assert.equal(speed.secondsLeft(speedMatch(),phoneAhead+30_000),30);
  assert.equal(speed.secondsLeft(speedMatch(),phoneAhead+600_000),0);
});

test('no countdown in regular matches, on a computer turn, or when the match is over',()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  assert.equal(speed.clock(speedMatch({turn_seconds:null,turn_deadline:null}),'ann',nameOf,T0),null);
  assert.equal(speed.clock(speedMatch({turn_deadline:null,turn_of_player_indices:[1]}),'ann',nameOf,T0),null);
  assert.equal(speed.clock(speedMatch({status:'over'}),'ann',nameOf,T0),null);
});

test('the player whose turn it is sees their time, in red for the last 10 seconds',()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+5_000)},{text:'Your move: 0:55',urgent:false});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+49_000)},{text:'Your move: 0:11',urgent:false});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+50_000)},{text:'Your move: 10 s left',urgent:true});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+59_500)},{text:'Your move: 1 s left',urgent:true});
});

test('everyone else sees whose clock is running, never in red',()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  assert.deepEqual({...speed.clock(speedMatch(),'bob',nameOf,T0+55_000)},{text:'Ann: 0:05',urgent:false});
  assert.deepEqual({...speed.clock(speedMatch(),null,nameOf,T0+18_000)},{text:'Ann: 0:42',urgent:false});
  assert.deepEqual({...speed.clock(speedMatch({turn_of_player_indices:[0,1]}),'cy',nameOf,T0+45_000)},{text:'0:15',urgent:false});
});

test('the timed-out player gets their own message; the others see who was replaced',()=>{
  const speed=loadSpeed();
  const afterTakeover=speedMatch({players:[{player_index:0,kind:'computer',user_id:null},{player_index:1,kind:'human',user_id:'bob'}],
    turn_deadline:null,timeouts:[{player_index:0,user_id:'ann',at:'2026-01-01T00:01:00Z'}]});
  assert.equal(speed.notice(afterTakeover,'ann',nameOf),'You ran out of time. A computer is playing for you now.');
  assert.equal(speed.notice(afterTakeover,'bob',nameOf),'Time ran out: a computer took over for Ann');
  assert.equal(speed.notice(afterTakeover,null,nameOf),'Time ran out: a computer took over for Ann');
  const ended=speedMatch({status:'over',end_reason:'timed_out',turn_deadline:null,timeouts:[{player_index:0,user_id:'ann',at:'x'}]});
  for(const viewer of ['ann','bob',null]) assert.equal(speed.notice(ended,viewer,nameOf),'Match ended: time ran out');
  assert.equal(speed.notice(speedMatch(),'ann',nameOf),null);
  assert.equal(speed.notice(speedMatch({turn_seconds:null}),'ann',nameOf),null);
  assert.equal(speed.notice(speedMatch({status:'over',end_reason:'finished'}),'ann',nameOf),null);
});

// The create form, through the real portal.js (set up like portal.test.cjs).
function setupPortal(request) {
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id)) nodes.set(id,{value:'',hidden:false,disabled:false,dataset:{},children:[],classList:{toggle(){}},
      replaceChildren(...children){this.children=children;},setAttribute(){},addEventListener(){},focus(){}});
    return nodes.get(id);
  };
  const window={addEventListener(){}};
  const ctx=vm.createContext({console,Map,Set,URLSearchParams,Date,Math,Number,String,
    navigator:{onLine:true},localStorage:{},PortalOffline:class {api(...args){return request(...args);}read(){return {};}write(){}},
    document:{getElementById:node,body:{classList:{toggle(){}}}},window,
    location:{hash:'',search:'',assign(){}},history:{replaceState(){}},
    Auth:{current:()=>({id:'owner'})},I18n,GameFrame:class{load(){}unload(){}send(){}},
    apiRequest:request,formatTime:()=>'',shortId:x=>x,showPageError(){},el:(tag,attrs={},...children)=>({...attrs,children})});
  vm.runInContext(read('portal-speed.js'),ctx);
  vm.runInContext(read('portal.js').replace(/main\(\);\s*$/,''),ctx);
  const run=code=>vm.runInContext(code,ctx);
  run('refresh=async()=>{};state.games=[{id:"game",version:2,name:"Duel",allowed_player_counts:[2]}]');
  node('new-game').value='game';
  return {node,run};
}

test('creating a speed match sends turn_seconds; Regular sends nothing new',async()=>{
  for(const [choice,expected] of [['',undefined],['60',60],['180',180],['600',600]]) {
    const bodies=[];
    const {node,run}=setupPortal(async(method,url,opts)=>{bodies.push(opts.body);return {id:'match',game_version:2,players:[{}]};});
    node('match-speed').value=choice;
    await run('createMatch()');
    assert.equal(bodies[0].turn_seconds,expected,`choice ${JSON.stringify(choice)}`);
    assert.equal('turn_seconds' in bodies[0],expected!==undefined);
  }
});

test('the choice is hidden for solo-only games',()=>{
  const {node,run}=setupPortal(async()=>({}));
  run('updateMatchSetup()');
  assert.equal(node('speed-choice').hidden,false);
  run('state.games[0].allowed_player_counts=[1];updateMatchSetup()');
  assert.equal(node('speed-choice').hidden,true);
});

test('the offline cache lists the speed files and the page loads them before portal.js',()=>{
  const sw=read('portal-sw.js'),html=read('portal.html');
  for(const file of ['portal-speed.js','portal-speed.css']) {
    const version=html.match(new RegExp(`/static/${file.replace('.','\\.')}\\?v=\\d+`))[0];
    assert.ok(sw.includes(`"${version}"`),`${version} in portal-sw.js`);
  }
  assert.ok(html.indexOf('portal-speed.js')<html.indexOf('/static/portal.js'));
});
