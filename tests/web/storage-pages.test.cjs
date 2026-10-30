const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup(overrides={}) {
  const context={URLSearchParams,Map,AbortController,setTimeout,clearTimeout,fetch:async()=>({ok:true,json:async()=>({pagination:true})})};
  Object.assign(context,overrides);
  context.window=context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/storage-pages.js'),'utf8'),context);
  return context.StoragePages;
}

test('paged catalogs request summaries in bounded pages and preserve credentials',async()=>{
  const pages=setup(),calls=[];
  const user={id:'alice',password:'secret'};
  const rows=await pages.list(async(...args)=>{calls.push(args);return {items:[{id:'a'}],next_cursor:'next'};},'/games',{user});
  assert.equal(rows.length,1);
  assert.equal(calls[0][1],'/v2/games?limit=50');
  assert.equal(calls[0][2].user,user);
});

test('catalog search is scoped per signed-in user and resets to its first page',async()=>{
  const pages=setup(),calls=[];
  pages.search('/games',{id:'alice'},'Chess');
  const request=async(method,url)=>{calls.push(url);return {items:[],next_cursor:null};};
  await pages.list(request,'/games',{user:{id:'alice'}});
  await pages.list(request,'/games',{user:{id:'bob'}});
  assert.match(calls[0],/q=chess/);
  assert.doesNotMatch(calls[1],/q=/);
});


test('unresponsive capability discovery aborts and falls back to the existing API',async()=>{
 let aborted=false;
 const pages=setup({
  setTimeout:fn=>{queueMicrotask(fn);return 1;},clearTimeout:()=>{},
  fetch:(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>{aborted=true;reject(new Error('aborted'));}))
 });
 const calls=[];
 const rows=await pages.list(async(_method,url)=>{calls.push(url);return [];},'/games');
 assert.equal(aborted,true);assert.deepEqual(calls,['/games']);assert.deepEqual(rows,[]);
});
