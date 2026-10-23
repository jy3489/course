const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

const LOCALES=[{code:'en',name:'English',direction:'ltr'},{code:'es',name:'Español',direction:'ltr'},{code:'ar',name:'العربية',direction:'rtl'}];
const CATALOGS={
  es:{version:'es1',messages:{'ui.action.save':'Guardar','ui.match.moves_count':'Movimientos: {count}'}},
  ar:{version:'ar1',messages:{'ui.action.save':'حفظ'}},
};

// A tiny fake browser: just what static/i18n.js touches.
function element(tag){
  return {tag,children:[],attrs:{},dataset:{},textContent:'',className:'',value:'',listeners:{},
    setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return k in this.attrs?this.attrs[k]:null;},
    addEventListener(type,fn){this.listeners[type]=fn;},append(...n){this.children.push(...n);},
    replaceChildren(...n){this.children=n;},insertBefore(n){this.children.push(n);}};
}

// Objects made inside the sandbox have another realm's prototypes; compare their JSON.
const plain=value=>JSON.parse(JSON.stringify(value));

function setup({stored={},languages=['en-US'],user=null,accountLocale='',catalogs=CATALOGS}={}){
  const storage=new Map(Object.entries(stored).map(([k,v])=>[k,JSON.stringify(v)]));
  const requests=[],apiCalls=[],events=[];
  const topbar=element('header');
  const translatable=[],withPlaceholder=[];
  const document={
    documentElement:{lang:'',dir:''},readyState:'complete',
    querySelector:selector=>selector==='.topbar'?topbar:null,
    getElementById:()=>null,
    createElement:element,
    querySelectorAll:selector=>selector==='[data-i18n]'?translatable:selector==='[data-i18n-placeholder]'?withPlaceholder:[],
    addEventListener(){},
    dispatchEvent(event){events.push(event);},
  };
  const nativeFetch=async(input,init={})=>{
    const url=typeof input==='string'?input:input.url;
    requests.push({url,headers:new Headers(init.headers??{})});
    if(url==='/i18n/locales')return {ok:true,json:async()=>({locales:LOCALES})};
    const match=/^\/i18n\/(\w+)$/.exec(url);
    if(match&&catalogs[match[1]])return {ok:true,json:async()=>catalogs[match[1]]};
    return {ok:false,json:async()=>({})};
  };
  const sandbox={
    console,Map,Set,JSON,Promise,URL,Headers,Request:class{},
    CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}},
    document,navigator:{languages},location:{href:'http://localhost/portal',origin:'http://localhost'},
    localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    fetch:nativeFetch,
    Auth:{current:()=>user},
    apiRequest:async(method,url,options)=>{apiCalls.push({method,url,body:options?.body});return method==='GET'?{locale:accountLocale}:{};},
  };
  sandbox.window=sandbox;
  sandbox.window.addEventListener=()=>{};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/i18n.js'),'utf8'),sandbox);
  const run=code=>vm.runInContext(code,sandbox);
  const settle=async()=>{await run('I18n.ready');await new Promise(resolve=>setImmediate(resolve));};
  return {run,settle,document,storage,requests,apiCalls,events,topbar,translatable,withPlaceholder,sandbox};
}

test('a saved choice beats the browser language',async()=>{
  const t=setup({stored:{'game-platform.locale.v1':'es'},languages:['ar']});await t.settle();
  assert.equal(t.run('I18n.locale()'),'es');assert.equal(t.document.documentElement.lang,'es');
});

test('without a choice the browser language is used, regional tags included, else English',async()=>{
  const spanish=setup({languages:['ja','es-MX','en']});await spanish.settle();
  assert.equal(spanish.run('I18n.locale()'),'es');
  const unsupported=setup({languages:['ja','ko']});await unsupported.settle();
  assert.equal(unsupported.run('I18n.locale()'),'en');
});

test('texts get their placeholders filled, then fall back to English, then to the key',async()=>{
  const t=setup({stored:{'game-platform.locale.v1':'es'}});await t.settle();
  assert.equal(t.run('I18n.t("ui.match.moves_count",{count:3})'),'Movimientos: 3');
  assert.equal(t.run('I18n.t("ui.missing",{},"English text")'),'English text');
  assert.equal(t.run('I18n.t("ui.missing")'),'ui.missing');
  assert.equal(t.run('I18n.t("ui.match.moves_count",{},"x")'),'Movimientos: {count}');
});

test('the page text is translated, and English restores what the HTML said',async()=>{
  const t=setup({stored:{'game-platform.locale.v1':'es'}});
  const save={dataset:{i18n:'ui.action.save'},textContent:'Save'};
  const search={attrs:{placeholder:'Search',  'data-i18n-placeholder':'ui.action.search'},
    getAttribute(k){return this.attrs[k]??null;},setAttribute(k,v){this.attrs[k]=v;}};
  t.translatable.push(save);t.withPlaceholder.push(search);
  await t.settle();
  assert.equal(save.textContent,'Guardar');
  assert.equal(search.attrs.placeholder,'Search'); // no Spanish text for this key: stays English
  await t.run('I18n.choose("en")');
  assert.equal(save.textContent,'Save');
});

test('choosing a language saves it, switches direction and tells the page',async()=>{
  const t=setup();await t.settle();
  await t.run('I18n.choose("ar")');
  assert.equal(JSON.parse(t.storage.get('game-platform.locale.v1')),'ar');
  assert.equal(t.document.documentElement.lang,'ar');assert.equal(t.document.documentElement.dir,'rtl');
  assert.ok(t.events.some(event=>event.type==='i18n-changed'&&event.detail.locale==='ar'));
  await t.run('I18n.choose("es")');
  assert.equal(t.document.documentElement.dir,'ltr');
  assert.equal(JSON.parse(t.storage.get('game-platform.i18n-catalog.v1:es')).version,'es1'); // kept for offline use
});

test('the picker lists every language by its own name',async()=>{
  const t=setup();await t.settle();
  const wrapper=t.topbar.children.find(node=>node.className==='language-picker');
  const select=wrapper.children.find(node=>node.tag==='select');
  assert.deepEqual(select.children.map(option=>option.textContent),['English','Español','العربية']);
  assert.equal(select.attrs['data-i18n-aria-label'],'ui.language');
});

test('API requests say which language the page wants; static files are left alone',async()=>{
  const t=setup({stored:{'game-platform.locale.v1':'es'}});await t.settle();
  t.requests.length=0;
  await t.run('fetch("/games")');await t.run('fetch("/static/portal.js")');await t.run('fetch("https://elsewhere.example/x")');
  assert.deepEqual(t.requests.map(r=>[r.url,r.headers.get('accept-language')]),
    [['/games','es'],['/static/portal.js',null],['https://elsewhere.example/x',null]]);
});

test('a logged-in choice is saved on the account',async()=>{
  const t=setup({user:{id:'u1',password:'p'}});await t.settle();t.apiCalls.length=0;
  await t.run('I18n.choose("es")');
  assert.deepEqual(plain(t.apiCalls.filter(call=>call.method==='PATCH').map(call=>[call.url,call.body])),[['/users/u1',{locale:'es'}]]);
});

test('logging in applies the account language over this browser\'s',async()=>{
  const t=setup({user:{id:'u1',password:'p'},accountLocale:'ar',stored:{'game-platform.locale.v1':'es'}});
  await t.settle();
  assert.equal(t.run('I18n.locale()'),'ar');
  assert.equal(JSON.parse(t.storage.get('game-platform.locale.v1')),'ar');
});

test('an account without a language gets this browser\'s explicit choice, but not a guess',async()=>{
  const chosen=setup({user:{id:'u1',password:'p'},accountLocale:'',stored:{'game-platform.locale.v1':'es'}});
  await chosen.settle();
  assert.deepEqual(plain(chosen.apiCalls.filter(call=>call.method==='PATCH').map(call=>call.body)),[{locale:'es'}]);
  const guessed=setup({user:{id:'u1',password:'p'},accountLocale:'',languages:['es']});
  await guessed.settle();
  assert.equal(guessed.apiCalls.filter(call=>call.method==='PATCH').length,0);
});

test('offline, the saved catalog still translates the page',async()=>{
  const online=setup({stored:{'game-platform.locale.v1':'es'}});await online.settle();
  const saved=online.storage.get('game-platform.i18n-catalog.v1:es');
  const offline=setup({stored:{'game-platform.locale.v1':'es','game-platform.i18n-catalog.v1:es':JSON.parse(saved)},catalogs:{}});
  await offline.settle();
  assert.equal(offline.run('I18n.t("ui.action.save")'),'Guardar');
});
