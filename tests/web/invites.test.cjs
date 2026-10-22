// Invite links: the inviter in "ref" is remembered and sent when an account is created.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

const AUTH_JS=fs.readFileSync(path.join(__dirname,'../../static/auth.js'),'utf8');

/** Loads auth.js as if the page were opened at [hash], with [storage] as localStorage. */
function loadAuth(hash, storage=new Map()) {
  const ctx=vm.createContext({console,URLSearchParams,CustomEvent:class{constructor(type){this.type=type;}},
    localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
    location:{hash,search:'',origin:'https://buildplay.fun'},
    window:{addEventListener(){}},document:{dispatchEvent(){}},
    apiRequest:async()=>{},el:()=>({})});
  vm.runInContext(AUTH_JS+'\nthis.Auth=Auth;',ctx);
  return {Auth:ctx.Auth,storage};
}

test('an invite link remembers who sent it, and new accounts carry the inviter',()=>{
  const {Auth}=loadAuth('#match=m1&ref=ann');
  assert.deepEqual(
    {...Auth.newAccountBody({display_name:'Bob'})},
    {display_name:'Bob',invited_by_user_id:'ann'},
  );
});

test('the first invite link counts; a later one does not replace it',()=>{
  const {storage}=loadAuth('#ref=ann');
  const {Auth}=loadAuth('#ref=cy',storage);
  assert.equal(Auth.newAccountBody({}).invited_by_user_id,'ann');
});

test('opening your own invite link does not make you your own inviter',()=>{
  const storage=new Map([['game-platform.accounts.v1',JSON.stringify([{id:'ann',display_name:'Ann',password:'p',linked:[]}])]]);
  const {Auth}=loadAuth('#ref=ann',storage);
  assert.deepEqual({...Auth.newAccountBody({display_name:'x'})},{display_name:'x'});
});

test('without an invite link, request bodies are unchanged',()=>{
  const {Auth}=loadAuth('#match=m1');
  assert.deepEqual({...Auth.newAccountBody({display_name:'x'})},{display_name:'x'});
});

test('logging in forgets the remembered invite',()=>{
  const {Auth,storage}=loadAuth('#ref=ann');
  Auth.upsert({user_id:'bob',display_name:'Bob',password:'pw',linked_accounts:[]});
  assert.equal(storage.has('game-platform.invited-by.v1'),false);
  assert.deepEqual({...Auth.newAccountBody({})},{});
});

test('invite links point at the portal and keep the match',()=>{
  const {Auth}=loadAuth('');
  assert.equal(Auth.inviteLink({id:'ann'}),'https://buildplay.fun/portal#ref=ann');
  assert.equal(Auth.inviteLink({id:'ann'},'match=m1'),'https://buildplay.fun/portal#match=m1&ref=ann');
});
