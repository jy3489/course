const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../static/common.js'),'utf8');

function fitter(width,height,viewportWidth=390,viewportHeight=650) {
  let scale=1,measures=0;const callbacks={},queue=[];
  const root={style:{setProperty(key,value){assert.equal(key,'zoom');scale=Number(value);}}};
  const body={scrollWidth:width,scrollHeight:height,getBoundingClientRect(){measures++;return {left:0,top:0,right:body.scrollWidth*scale,bottom:body.scrollHeight*scale};}};
  const ctx=vm.createContext({document:{documentElement:root,body},innerWidth:viewportWidth,innerHeight:viewportHeight,
    addEventListener:(name,fn)=>callbacks[name]=fn,requestAnimationFrame:fn=>queue.push(fn),
    MutationObserver:class {constructor(fn){callbacks.mutation=fn;}observe(){}},
    ResizeObserver:class {constructor(fn){callbacks.resizeBody=fn;}observe(){}}});
  vm.runInContext(source.slice(source.indexOf('function fitGameViewport()'),source.indexOf('\nclass GameFrame')),ctx);
  vm.runInContext('fitGameViewport()',ctx);
  return {body,root,ctx,callbacks,queue,scale:()=>scale,measures:()=>measures,flush(){while(queue.length)queue.shift()();}};
}

test('oversized boards fit both dimensions while responsive games retain their size',()=>{
  const large=fitter(600,900);large.flush();
  assert.ok(600*large.scale()<=390);assert.ok(900*large.scale()<=650);
  const small=fitter(300,400);small.flush();assert.equal(small.scale(),1);
});

test('rotation and growing game content trigger a new fit without reloading the game',()=>{
  const t=fitter(600,900);t.flush();
  t.ctx.innerWidth=667;t.ctx.innerHeight=300;t.callbacks.resize();t.flush();
  assert.ok(900*t.scale()<=300);
  t.body.scrollHeight=1600;t.callbacks.mutation([{target:t.body}]);t.flush();
  assert.ok(1600*t.scale()<=300);
  t.body.scrollHeight=200;t.body.scrollWidth=300;t.callbacks.resizeBody();t.flush();
  assert.equal(t.scale(),1);
});

test('fit style writes do not recursively schedule fits and repeated updates share a frame',()=>{
  const t=fitter(600,900);t.flush();
  t.callbacks.mutation([{target:t.root}]);assert.equal(t.queue.length,0);
  t.callbacks.resize();t.callbacks.resizeBody();t.callbacks.load();assert.equal(t.queue.length,1);
  t.flush();assert.ok(t.measures()<20);
});

test('fitting stays inside the script-only sandbox and preserves the move protocol',()=>{
  const calls=[],nodes=[];
  const context=vm.createContext({window:{addEventListener(){}},document:{createElement(tag){
    const node={tag,contentWindow:{},setAttribute(k,v){this[k]=v;},addEventListener(){}};nodes.push(node);return node;
  }},Node:class{},container:{replaceChildren(node){this.child=node;}},onMove:move=>calls.push(move),onBad:assert.fail});
  vm.runInContext(source,context);vm.runInContext('frame = new GameFrame(container,onMove,onBad);frame.load("<h1>Game</h1>")',context);
  assert.equal(nodes[0].sandbox,'allow-scripts');assert.match(nodes[0].srcdoc,/fitGameViewport/);
  vm.runInContext('frame.receive({source:frame.iframe.contentWindow,data:{type:"make_move",new_state:{n:1},next_turn_player_indices:[0]}})',context);
  assert.equal(calls.length,1);assert.equal(calls[0].new_state.n,1);
});
