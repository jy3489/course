const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../static/common.js'),'utf8');
function setup(mobile=true) {
  const events={},created=[],classes=new Set();
  const node=()=>({attrs:{},events:{},inert:false,children:[],setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.events[k]=fn;},prepend(n){this.children.unshift(n);},append(n){this.children.push(n);},focus(){this.focused=true;},querySelectorAll(){return this.children;},getClientRects(){return [1];}});
  const sidebar=node(),main=node(),toolbar=node(),body=node(),matchbar=node();
  body.classList={toggle(k,v){v?classes.add(k):classes.delete(k);}};
  const media={matches:mobile,addEventListener(k,fn){events.media=fn;}};
  const document={body,querySelector(selector){return selector.includes('> .sidebar')?sidebar:selector==='.work'?main:selector==='.topbar'?toolbar:selector==='.match-toolbar'?matchbar:{};},addEventListener(k,fn){events[k]=fn;}};
  const ctx=vm.createContext({document,window:{matchMedia:()=>media},el(tag,attrs){const n=node();Object.assign(n,attrs);if(attrs.onclick)n.events.click=attrs.onclick;created.push(n);return n;}});
  vm.runInContext(source.slice(source.indexOf('function setUpSidebarDrawer()'),source.indexOf("\nif (document.readyState")),ctx);
  vm.runInContext('setUpSidebarDrawer()',ctx);
  return {sidebar,main,toolbar,matchbar,created,events,media,classes,document};
}
test('mobile drawer opens from either toolbar and closes without leaving main content interactive',()=>{
  const t=setup();assert.equal(t.sidebar.inert,true);
  t.toolbar.children[0].events.click();assert.ok(t.classes.has('sidebar-open'));
  assert.equal(t.main.inert,true);assert.equal(t.sidebar.inert,false);
  t.sidebar.children[0].events.click();assert.equal(t.main.inert,false);assert.equal(t.sidebar.inert,true);
  t.matchbar.children[0].events.click();assert.ok(t.classes.has('sidebar-open'));
  t.created.find(n=>n.class==='sidebar-backdrop').events.click();assert.equal(t.classes.has('sidebar-open'),false);
});
test('Escape and choosing a game close the drawer and restore the opener',()=>{
  const t=setup(),button=t.toolbar.children[0];button.events.click();
  t.events.keydown({key:'Escape',preventDefault(){}});assert.equal(t.classes.has('sidebar-open'),false);assert.equal(button.focused,true);
  button.events.click();t.sidebar.events.click({target:{closest:()=>({})}});assert.equal(t.main.inert,false);
});
test('desktop web uses the same open and close controls as mobile',()=>{
  const t=setup(false);assert.equal(t.sidebar.inert,true);
  t.toolbar.children[0].events.click();
  assert.equal(t.sidebar.inert,false);assert.equal(t.main.inert,true);assert.equal(t.sidebar.attrs['aria-hidden'],'false');
  assert.equal(t.sidebar.attrs.role,'dialog');
  t.sidebar.children[0].events.click();assert.equal(t.classes.has('sidebar-open'),false);
  assert.equal(t.main.inert,false);assert.equal(t.sidebar.inert,true);
});
