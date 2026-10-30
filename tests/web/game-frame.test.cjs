const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
function browserContext(sandbox) {
  return vm.createContext({URLSearchParams, location:{search:''}, ...sandbox});
}
const source=fs.readFileSync(path.join(__dirname,'../../static/common.js'),'utf8');

function reporter(width,height) {
  const callbacks={},queue=[],messages=[];
  const root={};
  const body={scrollWidth:width,scrollHeight:height,getBoundingClientRect(){return {left:0,top:0,right:body.scrollWidth,bottom:body.scrollHeight};}};
  const ctx=browserContext({document:{documentElement:root,body},innerWidth:390,innerHeight:480,
    getComputedStyle:()=>({marginRight:'0',marginBottom:'0'}),parent:{postMessage:m=>messages.push(m)},
    addEventListener:(name,fn)=>callbacks[name]=fn,requestAnimationFrame:fn=>queue.push(fn),
    MutationObserver:class {constructor(fn){callbacks.mutation=fn;}observe(){}},
    ResizeObserver:class {constructor(fn){callbacks.resizeBody=fn;}observe(){}}});
  vm.runInContext(source.slice(source.indexOf('function fitGameViewport()'),source.indexOf('\nclass GameFrame')),ctx);
  vm.runInContext('fitGameViewport()',ctx);
  return {body,ctx,callbacks,queue,messages,flush(){while(queue.length)queue.shift()();}};
}

function host(builder=false,initialHeight=480) {
  const iframe={style:{},contentWindow:{}};
  let focused=false;
  const container={id:builder?'test-frame':'game-box',clientWidth:390,clientHeight:initialHeight,
    get offsetHeight(){return this.clientHeight+2;},style:{removeProperty(){container.clientHeight=initialHeight;},setProperty(k,v){container.clientHeight=parseFloat(v)-2;}}};
  const ctx=browserContext({window:{addEventListener(){}},document:{body:{classList:{contains:()=>focused}}},container,iframe});
  vm.runInContext(source,ctx);
  vm.runInContext('frame=new GameFrame(container,()=>{},()=>{});frame.iframe=iframe;frame.resizeViewport()',ctx);
  return {iframe,container,ctx,focus(value){focused=value;vm.runInContext('frame.resizeViewport()',ctx);},report(width,height,extra={}){
    ctx.message={type:'game_viewport',width,height,viewport_width:parseInt(iframe.style.width),viewport_height:parseInt(iframe.style.height),...extra};
    vm.runInContext('frame.receive({source:iframe.contentWindow,data:message})',ctx);
  },view(){return vm.runInContext('frame.viewport',ctx);}};
}

test('sandbox measurement never zooms or mutates the game layout and deduplicates reports',()=>{
  const t=reporter(600,900);t.flush();
  assert.equal(t.messages.length,1);assert.equal(t.messages[0].height,900);
  t.callbacks.mutation();t.callbacks.resizeBody();t.callbacks.load();assert.equal(t.queue.length,1);
  t.flush();assert.equal(t.messages.length,1);
  t.body.scrollHeight=1000;t.callbacks.resizeBody();t.flush();assert.equal(t.messages.at(-1).height,1000);
});

test('fixed boards and responsive chess-style boards settle without resetting their scale',()=>{
  const t=host();t.report(600,900);t.report(600,900);
  assert.ok(600*t.view().scale<=390);assert.ok(900*t.view().scale<=480);
  const chess=host();
  for(let i=0;i<10;i++){
    const width=parseInt(chess.iframe.style.width),height=parseInt(chess.iframe.style.height);
    chess.report(width,Math.max(height,Math.min(560,width-24)+240));
  }
  const scale=chess.view().scale;
  assert.ok(scale>.5 && scale<1);assert.ok(chess.view().passes<8);
  for(let i=0;i<20;i++)chess.report(parseInt(chess.iframe.style.width),parseInt(chess.iframe.style.height));
  assert.equal(chess.view().scale,scale);
  chess.container.clientWidth=667;chess.container.clientHeight=260;
  vm.runInContext('frame.resizeViewport()',chess.ctx);
  assert.equal(chess.view().scale,1);assert.equal(chess.iframe.style.width,'667px');
});

test('viewport-dependent games are bounded and stale or invalid resize messages are ignored',()=>{
  const t=host();t.report(Infinity,900);t.report(900,900,{viewport_width:100});
  assert.equal(t.view().scale,1);
  vm.runInContext('frame.receive({source:{},data:{type:"game_viewport",width:900,height:900}})',t.ctx);
  assert.equal(t.view().scale,1);
  for(let i=0;i<30;i++)t.report(parseInt(t.iframe.style.width),parseInt(t.iframe.style.height)+40);
  assert.equal(t.view().passes,8);assert.ok(t.view().scale>=.1);
});

test('fitting stays inside the script-only sandbox and preserves the move protocol',()=>{
  const calls=[],nodes=[];
  const context=browserContext({window:{addEventListener(){}},document:{createElement(tag){
    const node={tag,contentWindow:{},style:{},setAttribute(k,v){this[k]=v;},addEventListener(){}};nodes.push(node);return node;
  }},Node:class{},container:{replaceChildren(node){this.child=node;}},onMove:move=>calls.push(move),onBad:assert.fail});
  vm.runInContext(source,context);vm.runInContext('frame = new GameFrame(container,onMove,onBad);frame.load("<h1>Game</h1>")',context);
  assert.equal(nodes[0].sandbox,'allow-scripts');assert.match(nodes[0].srcdoc,/fitGameViewport/);
  vm.runInContext('frame.receive({source:frame.iframe.contentWindow,data:{type:"make_move",new_state:{n:1},next_turn_player_indices:[0]}})',context);
  assert.equal(calls.length,1);assert.equal(calls[0].new_state.n,1);
});

test('inline Builder grows for a portrait game instead of shrinking its width',()=>{
  const t=host(true,214);
  t.report(390,760);
  assert.equal(t.view().scale,1);
  assert.equal(t.iframe.style.width,'390px');assert.equal(t.container.clientHeight,762);
  for(let i=0;i<20;i++)t.report(390,t.container.clientHeight+1);
  assert.equal(t.container.clientHeight,762); // 100vh plus rounding must not grow forever
  assert.equal(t.view().passes,1);
  t.focus(true);t.report(390,900);
  assert.ok(t.view().scale<1); // Focus still fits its fixed viewport
  t.focus(false);t.report(390,760);
  assert.equal(t.view().scale,1);assert.equal(t.container.clientHeight,762);
});

test('inline fixed-width games fit width before calculating natural preview height',()=>{
  const t=host(true);t.report(600,900);
  assert.equal(t.container.clientHeight,480);assert.equal(t.view().scale,.65);
  t.report(600,900);assert.equal(t.container.clientHeight,587);
  assert.equal(t.view().scale,.65);assert.equal(t.iframe.style.width,'600px');
});

test('inline previews bound viewport-dependent growth and reset for a narrower preset',()=>{
  const t=host(true);
  for(let i=0;i<30;i++)t.report(parseInt(t.iframe.style.width),parseInt(t.iframe.style.height)+40);
  assert.equal(t.view().passes,8);assert.equal(t.view().scale,1);
  t.container.clientWidth=320;vm.runInContext('frame.resizeViewport()',t.ctx);
  assert.equal(t.container.clientHeight,480);assert.equal(t.view().passes,0);
});

test('observed resize work is deferred and independent of page scroll position',()=>{
  const observers=[],queue=[];let measurements=0;
  const container={};
  const ctx=browserContext({window:{addEventListener(){}},document:{},container,
    requestAnimationFrame:fn=>queue.push(fn),ResizeObserver:class{constructor(fn){observers.push(fn);}observe(){}}});
  vm.runInContext(source,ctx);vm.runInContext('frame=new GameFrame(container,()=>{},()=>{})',ctx);
  ctx.measure=()=>measurements++;vm.runInContext('frame.resizeViewport=measure',ctx);
  observers[0]();observers[0]();assert.equal(measurements,0);assert.equal(queue.length,1);
  queue.shift()();assert.equal(measurements,1);
  assert.doesNotMatch(source.slice(source.indexOf('class GameFrame'),source.indexOf('function stateChangedMessage')),/getBoundingClientRect|scrollTop/);
});
