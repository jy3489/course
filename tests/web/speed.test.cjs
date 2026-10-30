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

test('the player whose turn it is always sees "Your move", red for the last 10 seconds',()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+5_000)},{text:'Your move: 0:55',urgent:false,mine:true});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+49_000)},{text:'Your move: 0:11',urgent:false,mine:true});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+50_000)},{text:'Your move: 0:10',urgent:true,mine:true});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+51_000)},{text:'Your move: 0:09',urgent:true,mine:true});
  assert.deepEqual({...speed.clock(speedMatch(),'ann',nameOf,T0+59_500)},{text:'Your move: 0:01',urgent:true,mine:true});
});

test('everyone else sees whose clock is running, also red for the last 10 seconds',()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  assert.deepEqual({...speed.clock(speedMatch(),null,nameOf,T0+18_000)},{text:'Ann: 0:42',urgent:false,mine:false});
  assert.deepEqual({...speed.clock(speedMatch(),'bob',nameOf,T0+49_000)},{text:'Ann: 0:11',urgent:false,mine:false});
  assert.deepEqual({...speed.clock(speedMatch(),'bob',nameOf,T0+50_000)},{text:'Ann: 0:10',urgent:true,mine:false});
  assert.deepEqual({...speed.clock(speedMatch(),'bob',nameOf,T0+51_000)},{text:'Ann: 0:09',urgent:true,mine:false});
  assert.deepEqual({...speed.clock(speedMatch({turn_of_player_indices:[0,1]}),'cy',nameOf,T0+51_000)},{text:'0:09',urgent:true,mine:false});
});

/** A fake browser for the alert: records sounds, vibrations, notifications and titles. */
function fakeBrowser({hidden=false,permission='granted',active=true}={}) {
  const log={beeps:0,vibrations:[],notifications:[],titles:[]};
  let titleNow='Stack Duel · Portal';const timers=new Map();let nextTimer=1;
  const document={get title(){return titleNow;},set title(t){titleNow=t;log.titles.push(t);},hidden};
  class AudioContext{constructor(){this.currentTime=0;this.destination={};}
    createOscillator(){return {frequency:{},connect:x=>x,start(){log.beeps++;},stop(){}};}
    createGain(){return {gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect:x=>x};}}
  const env={document,window:{addEventListener(){}},AudioContext,
    navigator:{userActivation:{hasBeenActive:active},vibrate:ms=>log.vibrations.push(ms),
      serviceWorker:{ready:Promise.resolve({showNotification:(title,options)=>log.notifications.push([title,options.body])})}},
    Notification:{permission},
    setInterval:(f)=>{const id=nextTimer++;timers.set(id,f);return id;},clearInterval:id=>timers.delete(id)};
  return {env,log,tick:()=>[...timers.values()].forEach(f=>f()),title:()=>titleNow,timers};
}
const flush=()=>new Promise(r=>setTimeout(r,0));

test('the 10-second alert fires exactly once for the player to move',async()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  const b=fakeBrowser({hidden:true});const alerter=speed.createAlerter(b.env);
  for(const t of [5_000,49_000,49_900]) alerter.update(speedMatch(),'ann','Stack Duel',T0+t);
  assert.equal(b.log.beeps,0);
  for(const t of [50_000,50_250,52_000,55_000,58_000]) alerter.update(speedMatch(),'ann','Stack Duel',T0+t);
  await flush();
  assert.equal(b.log.beeps,1);
  assert.deepEqual(b.log.vibrations,[200]);
  assert.deepEqual(b.log.notifications,[['Stack Duel','Your move in Stack Duel: 10 seconds left!']]);
  assert.equal(b.title(),'⏰ 10 s left!');
  b.tick();assert.equal(b.title(),'Stack Duel · Portal');b.tick();assert.equal(b.title(),'⏰ 10 s left!');
  // Time's up: the title comes back.
  alerter.update(speedMatch(),'ann','Stack Duel',T0+61_000);
  assert.equal(b.title(),'Stack Duel · Portal');assert.equal(b.timers.size,0);
});

test('no alert for the waiting player',async()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  const b=fakeBrowser({hidden:true});const alerter=speed.createAlerter(b.env);
  for(const t of [50_000,52_000,59_000]) alerter.update(speedMatch(),'bob','Stack Duel',T0+t);
  await flush();
  assert.deepEqual(b.log,{beeps:0,vibrations:[],notifications:[],titles:[]});
});

test('moving stops the alert, and the next turn alerts again only at its own 10 seconds',async()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  const b=fakeBrowser();const alerter=speed.createAlerter(b.env);
  alerter.update(speedMatch(),'ann','Stack Duel',T0+50_000);
  assert.equal(b.title(),'⏰ 10 s left!');
  // Ann moved: it's Bob's turn with a new deadline.
  const bobsTurn=speedMatch({turn_of_player_indices:[1],turn_deadline:'2026-01-01T00:01:55Z'});
  alerter.update(bobsTurn,'ann','Stack Duel',T0+55_000);
  assert.equal(b.title(),'Stack Duel · Portal');assert.equal(b.timers.size,0);
  for(const t of [100_000,106_000]) alerter.update(bobsTurn,'ann','Stack Duel',T0+t);
  assert.equal(b.log.beeps,1);
  // Ann's next turn: one more alert at its own 10 seconds.
  const annAgain=speedMatch({turn_deadline:'2026-01-01T00:03:00Z'});
  alerter.update(annAgain,'ann','Stack Duel',T0+169_000);assert.equal(b.log.beeps,1);
  alerter.update(annAgain,'ann','Stack Duel',T0+170_000);alerter.update(annAgain,'ann','Stack Duel',T0+171_000);
  assert.equal(b.log.beeps,2);assert.deepEqual(b.log.vibrations,[200,200]);
});

test('no sound before any interaction, and no notification unless the page is hidden and permission was granted',async()=>{
  const speed=loadSpeed();speed.observe(speedMatch(),T0);
  for(const options of [{active:false,hidden:false},{hidden:true,permission:'default'},{hidden:true,permission:'denied'}]) {
    const b=fakeBrowser(options);const alerter=speed.createAlerter(b.env);
    alerter.update(speedMatch(),'ann','Stack Duel',T0+50_000);await flush();
    assert.equal(b.log.notifications.length,0,JSON.stringify(options));
    assert.equal(b.log.beeps,options.active===false?0:1);
    assert.deepEqual(b.log.vibrations,[200]);
  }
});

test('the service worker loads the page from the network first and reloads pages after an update',()=>{
  const sw=read('portal-sw.js');
  assert.match(sw,/url\.pathname === "\/static\/portal\.html"[\s\S]*fetch\(event\.request/);
  assert.match(sw,/caches\.delete/);
  assert.match(sw,/page\.navigate\(page\.url\)/);
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
  const ctx=vm.createContext({StoragePages:{enabled:async()=>false,list:(request,path,options)=>request("GET",path,options),controls(){},search(){return false;}},console,Map,Set,URLSearchParams,Date,Math,Number,String,
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
