const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../static/common.js'),'utf8');

function reporter(width,height) {
  const callbacks={},queue=[],messages=[];
  const root={};
  const body={scrollWidth:width,scrollHeight:height,getBoundingClientRect(){return {left:0,top:0,right:body.scrollWidth,bottom:body.scrollHeight};}};
  const ctx=vm.createContext({document:{documentElement:root,body},innerWidth:390,innerHeight:480,
    getComputedStyle:()=>({marginRight:'0',marginBottom:'0'}),parent:{postMessage:m=>messages.push(m)},
    addEventListener:(name,fn)=>callbacks[name]=fn,requestAnimationFrame:fn=>queue.push(fn),
    MutationObserver:class {constructor(fn){callbacks.mutation=fn;}observe(){}},
    ResizeObserver:class {constructor(fn){callbacks.resizeBody=fn;}observe(){}}});
  vm.runInContext(source.slice(source.indexOf('function fitGameViewport()'),source.indexOf('\nclass GameFrame')),ctx);
  vm.runInContext('fitGameViewport()',ctx);
  return {body,ctx,callbacks,queue,messages,flush(){while(queue.length)queue.shift()();}};
}

function host() {
  const iframe={style:{},contentWindow:{}};
  const container={clientWidth:390,clientHeight:480};
  const ctx=vm.createContext({window:{addEventListener(){}},document:{},container,iframe});
  vm.runInContext(source,ctx);
  vm.runInContext('frame=new GameFrame(container,()=>{},()=>{});frame.iframe=iframe;frame.resizeViewport()',ctx);
  return {iframe,container,ctx,report(width,height,extra={}){
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
  const context=vm.createContext({window:{addEventListener(){}},document:{createElement(tag){
    const node={tag,contentWindow:{},style:{},setAttribute(k,v){this[k]=v;},addEventListener(){}};nodes.push(node);return node;
  }},Node:class{},container:{replaceChildren(node){this.child=node;}},onMove:move=>calls.push(move),onBad:assert.fail});
  vm.runInContext(source,context);vm.runInContext('frame = new GameFrame(container,onMove,onBad);frame.load("<h1>Game</h1>")',context);
  assert.equal(nodes[0].sandbox,'allow-scripts');assert.match(nodes[0].srcdoc,/fitGameViewport/);
  vm.runInContext('frame.receive({source:frame.iframe.contentWindow,data:{type:"make_move",new_state:{n:1},next_turn_player_indices:[0]}})',context);
  assert.equal(calls.length,1);assert.equal(calls[0].new_state.n,1);
});

test('scrolling the builder cannot increase preview height or push its controls away',()=>{
  const events={};let top=200,height;
  const work={clientHeight:600,scrollTop:0,getBoundingClientRect:()=>({top:0})};
  const container={id:'test-frame',closest:()=>work,getBoundingClientRect:()=>({width:800,top}),style:{getPropertyValue(){return height;},setProperty(k,v){height=v;}}};
  const ctx=vm.createContext({window:{innerHeight:600,addEventListener(k,fn){events[k]=fn;}},document:{},container});
  vm.runInContext(source,ctx);vm.runInContext('new GameFrame(container,()=>{},()=>{})',ctx);
  events.resize();assert.equal(height,'380px');
  top=0;work.scrollTop=200;events.resize();assert.equal(height,'380px');
});

test('builder size observers defer writes and do not rewrite an unchanged height',()=>{
  const observers=[],queue=[];let height,writes=0;
  const work={clientHeight:650,scrollTop:0,getBoundingClientRect:()=>({top:0})};
  const container={id:'test-frame',closest:()=>work,getBoundingClientRect:()=>({width:390,top:250}),
    style:{getPropertyValue:()=>height,setProperty(k,v){writes++;height=v;}}};
  const ctx=vm.createContext({window:{innerHeight:650,addEventListener(){}},document:{},container,
    requestAnimationFrame:fn=>queue.push(fn),ResizeObserver:class{constructor(fn){observers.push(fn);}observe(){}}});
  vm.runInContext(source,ctx);vm.runInContext('new GameFrame(container,()=>{},()=>{})',ctx);
  observers[0]();observers[0]();assert.equal(writes,0);assert.equal(queue.length,1);
  queue.shift()();assert.equal(writes,1);assert.equal(height,'380px');
  observers[0]();queue.shift()();assert.equal(writes,1);
});
