const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../..');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');
function worker() {
  const handlers = {};
  const context = vm.createContext({self:{location:{origin:'https://example.test'},addEventListener:(name,fn)=>handlers[name]=fn},URL});
  vm.runInContext(read('static/portal-sw.js'), context);
  return {context,handlers,shell:Array.from(vm.runInContext('SHELL',context))};
}
test('every page script, style, manifest and install icon is precached at its exact URL',()=>{
  const {shell} = worker();
  const assets = [...read('static/portal.html').matchAll(/(?:src|href)="(\/static\/[^"#]+)"/g)].map(m=>m[1]);
  for(const asset of assets) assert.ok(shell.includes(asset), `missing ${asset}`);
  for(const asset of shell) assert.ok(fs.existsSync(path.join(root, asset.split('?')[0])), asset);
  const manifest=JSON.parse(read('static/portal.webmanifest'));
  assert.equal(manifest.start_url, '/static/portal.html');
  for(const size of [192,512]) {
    const icon=manifest.icons.find(i=>i.sizes===`${size}x${size}` && i.type==='image/png');
    assert.ok(icon);assert.ok(shell.includes(icon.src));
    const png=fs.readFileSync(path.join(root,icon.src));
    assert.equal(png.readUInt32BE(16),size);assert.equal(png.readUInt32BE(20),size);
  }
});
test('worker serves saved shell offline and leaves APIs and other students pages untouched',async()=>{
  const {context,handlers}=worker();
  const saved={body:'cached'};
  context.caches={open:async()=>({match:async()=>saved})};
  context.fetch=()=>{throw Error('offline')};
  let response;
  handlers.fetch({request:{method:'GET',url:'https://example.test/static/portal.html'},respondWith:p=>response=p});
  assert.equal(await response,saved);
  for(const [method,url] of [['GET','/matches'],['GET','/static/builder.html'],['POST','/matches/m/moves']]) {
    handlers.fetch({request:{method,url:'https://example.test'+url},respondWith(){assert.fail('request intercepted')}});
  }
});
function install(standalone=false) {
  const events={},nodes={};
  for(const id of ['install-portal','install-help','install-message']) nodes[id]={hidden:id==='install-portal',addEventListener:(event,fn)=>events[id+event]=fn};
  vm.runInNewContext(read('static/portal-install.js'),{document:{getElementById:id=>nodes[id]},navigator:{},window:{matchMedia:()=>({matches:standalone}),addEventListener:(event,fn)=>events[event]=fn}});
  return {events,nodes};
}
test('install prompt requires a click, handles dismissal, and accepts a later prompt',async()=>{
  const {events,nodes}=install();let calls=0,prevented=false;
  const prompt={preventDefault(){prevented=true},prompt:async()=>calls++,userChoice:Promise.resolve({outcome:'dismissed'})};
  events.beforeinstallprompt(prompt);assert.ok(prevented);assert.equal(calls,0);assert.equal(nodes['install-portal'].hidden,false);
  await events['install-portalclick']();assert.equal(calls,1);assert.equal(nodes['install-portal'].hidden,true);
  assert.match(nodes['install-message'].textContent,/later/);
  events.beforeinstallprompt(prompt);assert.equal(nodes['install-portal'].hidden,false);
  events.appinstalled();assert.equal(nodes['install-help'].hidden,true);
});
test('standalone apps hide installation help and prompt failure leaves manual instructions',async()=>{
  assert.equal(install(true).nodes['install-help'].hidden,true);
  const {events,nodes}=install();
  events.beforeinstallprompt({preventDefault(){},prompt:async()=>{throw Error('unavailable')}});
  await events['install-portalclick']();assert.match(nodes['install-message'].textContent,/browser/);assert.equal(nodes['install-portal'].disabled,false);
});
