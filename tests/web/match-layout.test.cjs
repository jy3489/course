const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup(isLandscape = false) {
  const nodes = new Map(), events = {}, media = {matches:isLandscape};
  const classes = () => {
    const set = new Set();
    return {toggle(name,on){if(on)set.add(name);else set.delete(name);},contains:name=>set.has(name)};
  };
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden:false,textContent:'',attrs:{},classList:classes(),events:{},
      setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,v){this.events[k]=v;},focus(){this.focused=true;}});
    return nodes.get(id);
  };
  node('match-title').textContent = 'Tic-Tac-Toe';
  media.addEventListener = (_, fn) => media.change = fn;
  const body = {classList:classes()};
  const context = {I18n:{t:(key,params,fallback)=>fallback??key},window:{matchMedia:()=>media},navigator:{onLine:true},
    document:{getElementById:node,body,addEventListener:(k,v)=>events[k]=v},
    MutationObserver:class{observe(){}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/portal-match-layout.js'),'utf8'), context);
  const layout = context.window.PortalMatchLayout;
  let exits = 0;
  layout.init(()=>exits++);
  return {layout,node,body,media,events,exitCount:()=>exits};
}
const ongoing = {id:'match',status:'ongoing',pending:0};

test('landscape focuses ongoing play; portrait and no-match reset the shell',()=>{
  const {layout,body,media} = setup(true);
  layout.update(ongoing); assert.ok(body.classList.contains('match-focus'));
  media.matches=false;media.change();assert.equal(body.classList.contains('match-focus'),false);
  layout.update(null);assert.equal(body.classList.contains('match-page'),false);
});
test('manual focus and Controls survive polling without resetting user choices',()=>{
  const {layout,node,body} = setup(true);
  layout.update(ongoing);
  node('match-focus-toggle').events.click();node('match-controls-toggle').events.click();
  layout.update({...ongoing});
  assert.equal(body.classList.contains('match-focus'),false);
  assert.equal(node('match-tools').hidden,false);
  assert.equal(node('match-controls-toggle').attrs['aria-expanded'],'true');
});
test('waiting lobby keeps Start and Join visible and never enters focus mode',()=>{
  const {layout,node,body} = setup(true);
  layout.update({id:'lobby',status:'waiting_for_players'});
  assert.equal(body.classList.contains('match-focus'),false);
  assert.equal(node('match-tools').hidden,false);
  assert.equal(node('match-controls-toggle').hidden,true);
});
test('pending and conflict status remain visible with controls closed',()=>{
  const {layout,node} = setup(true);
  layout.update({...ongoing,pending:2});layout.connection(false);
  assert.equal(node('match-tools').hidden,true);
  assert.match(node('match-play-notice').textContent,/2 moves saved/);
  layout.update({...ongoing,conflict:{message:'Server changed'}});
  assert.equal(node('match-tools').hidden,false);
  node('match-controls-toggle').events.click();layout.update({...ongoing,conflict:{message:'Server changed'}});
  assert.equal(node('match-tools').hidden,true);
  assert.match(node('match-play-notice').textContent,/Sync needs review/);
});
test('finished matches reveal rematch once, Escape restores control focus, Back exits',()=>{
  const {layout,node,events,exitCount} = setup(true);
  layout.update(ongoing);
  node('match-result').textContent='You won';layout.update({...ongoing,status:'over'});
  assert.equal(node('match-tools').hidden,false);
  assert.match(node('match-play-notice').textContent,/You won/);
  events.keydown({key:'Escape'});assert.ok(node('match-controls-toggle').focused);
  layout.update({...ongoing,status:'over'});assert.equal(node('match-tools').hidden,true);
  node('match-back').events.click();assert.equal(exitCount(),1);
});
