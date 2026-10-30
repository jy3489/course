const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup() {
  const context={URLSearchParams,Map,fetch:async()=>({ok:true,json:async()=>({pagination:true})})};
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
