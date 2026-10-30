const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function setup(){
  const nodes=new Map(),sent=[];let receive;
  const element=()=>({attributes:{},children:[],setAttribute(k,v){this.attributes[k]=v;},replaceChildren(...items){this.children=items;},addEventListener(k,f){this[k]=f;}});
  const document={getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},createElement:element};
  const parent={postMessage:m=>sent.push(m)},window={document,parent,addEventListener:(name,f)=>{receive=f;}};
  const code=fs.readFileSync(path.join(__dirname,'../../games/tictactoe.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(code,{window,document});
  const message={type:'state_changed',status:'ongoing',acting_for_player_index:0,my_player_index:0,turn_of_player_indices:[0],players:[{name:'You',player_index:0},{name:'Computer',player_index:1}],view:{board:['X','','','','','','','',''],winner:null,draw:false}};
  return {nodes,sent,send:extra=>receive({source:parent,data:{...message,...extra}})};
}
test('each board square announces its position and contents',()=>{
  const t=setup();t.send();const cells=t.nodes.get('board').children;
  assert.equal(cells[0].attributes['aria-label'],'Row 1, column 1: X');
  assert.equal(cells[8].attributes['aria-label'],'Row 3, column 3: empty');
  cells[1].click();assert.equal(t.sent.length,1);assert.equal(t.sent[0].move.cell,1);
});
test('history shows read-only status instead of waiting and cannot submit moves',()=>{
  const t=setup();t.send({replay:{index:5,total:6}});
  assert.equal(t.nodes.get('status').textContent,'Viewing move 5 of 6 · read only');
  assert.ok(t.nodes.get('board').children.every(c=>c.disabled));
  t.nodes.get('board').children[1].click();assert.equal(t.sent.length,0);
  t.send({replay:null});assert.match(t.nodes.get('status').textContent,/Your move/);
  assert.equal(t.nodes.get('board').children[1].disabled,false);
});
