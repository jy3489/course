const game={newMatch(ctx){const R=['A','2','3','4','5','6','7','8','9','10','J','Q','K'],S=['\u2660','\u2665','\u2666','\u2663'];let deck=[];R.forEach(r=>S.forEach(s=>deck.push(r+s)));for(let i=deck.length-1;i>0;i--){const j=Math.floor(ctx.random()*(i+1));const t=deck[i];deck[i]=deck[j];deck[j]=t;}
const n=ctx.players.length,per=n<=3?7:5,hands=[],books=[];for(let i=0;i<n;i++){hands.push([]);books.push([]);}
for(let k=0;k<per;k++)for(let i=0;i<n;i++)hands[i].push(deck.pop());
for(let i=0;i<n;i++)game._books(hands,books,i);
const state={deck,hands,books,log:['Game dealt! '+per+' cards each.'],last:null,winners:null};
return{state,turn:[ctx.players[0].id]};},
_rank(c){return c.slice(0,-1);},
_books(hands,books,i){const nb=[];let ch=true;while(ch){ch=false;const cnt={};hands[i].forEach(c=>{const r=c.slice(0,-1);cnt[r]=(cnt[r]||0)+1;});for(const r in cnt){if(cnt[r]===4){hands[i]=hands[i].filter(c=>c.slice(0,-1)!==r);books[i].push(r);nb.push(r);ch=true;break;}}}return nb;},
_refill(st,n2){n2=n2||5;for(let i=0;i<st.hands.length;i++){if(st.hands[i].length===0&&st.deck.length>0){const k=Math.min(n2,st.deck.length);for(let j=0;j<k;j++)st.hands[i].push(st.deck.pop());}}},
_totalBooks(st){return st.books.reduce((a,b)=>a+b.length,0);},
_isOver(st){if(game._totalBooks(st)>=13)return true;if(st.deck.length===0&&st.hands.every(h=>h.length===0))return true;return false;},
_winners(st){let m=Math.max.apply(null,st.books.map(b=>b.length));return st.books.map((b,i)=>b.length===m?i:-1).filter(i=>i>=0);},
_next(st,from){const n=st.hands.length;for(let k=1;k<=n;k++){const j=(from+k)%n;if(st.hands[j].length>0)return j;}if(st.deck.length>0)return(from+1)%n;return from;},
_payouts(st,ctx){const p={};const humans=ctx.players.filter(p=>p.kind!=='computer');humans.forEach(h=>p[h.id]=0);const pot=ctx.pot||0;if(pot<=0)return p;if(humans.length===0)return p;const w=st.winners||game._winners(st);const hw=w.map(i=>ctx.players[i]).filter(pp=>pp&&pp.kind!=='computer');const rec=hw.length>0?hw.map(h=>h.id):humans.map(h=>h.id);const base=Math.floor(pot/rec.length);let rem=pot-base*rec.length;rec.forEach(id=>{p[id]=base;});for(let k=0;k<rec.length&&rem>0;k++){p[rec[k]]++;rem--;}return p;},
applyMove(state,playerId,move,ctx){const idx=ctx.players.findIndex(p=>p.id===playerId);if(idx<0)throw new InvalidMove('unknown player');move=move||{};
const st={deck:state.deck.slice(),hands:state.hands.map(h=>h.slice()),books:state.books.map(b=>b.slice()),log:state.log.slice(),last:state.last,winners:state.winners};
const idOf=i=>ctx.players[i]?ctx.players[i].id:null;
if(st.hands[idx].length===0){
 if(st.deck.length>0){
  if(move.draw){const k=Math.min(5,st.deck.length);for(let j=0;j<k;j++)st.hands[idx].push(st.deck.pop());const nb=game._books(st.hands,st.books,idx);st.last={kind:'draw',from:idx,to:null,rank:'',got:0,fished:false,drew:null,extra:false,newB:nb,msg:'refill'};st.log.push(ctx.players[idx].display_name+' refilled +'+k);game._refill(st);return{state:st,turn:[playerId]};}
  throw new InvalidMove('empty hand: refill first');
 }else{
  if(move.pass){st.last={kind:'pass',from:idx,to:null,rank:'',got:0,fished:false,drew:null,extra:false,newB:[],msg:'pass'};st.log.push(ctx.players[idx].display_name+' passes');if(game._isOver(st)){st.winners=game._winners(st);return{state:st,turn:null,payouts:game._payouts(st,ctx)};}const nx=game._next(st,idx);return{state:st,turn:[idOf(nx)]};}
  throw new InvalidMove('you have no cards: pass');
 }}
const t=move.target,r=move.rank;
if(t==null||r==null)throw new InvalidMove('pick a rank and opponent');
if(!Number.isInteger(t)||t<0||t>=st.hands.length)throw new InvalidMove('bad opponent');
if(t===idx)throw new InvalidMove('cannot ask yourself');
if(['A','2','3','4','5','6','7','8','9','10','J','Q','K'].indexOf(r)<0)throw new InvalidMove('bad rank');
if(!st.hands[idx].some(c=>c.slice(0,-1)===r))throw new InvalidMove('you hold no '+r+'s');
const give=st.hands[t].filter(c=>c.slice(0,-1)===r);
let extra=false,drew=null,gotN=0,msg='';let newB=[];
if(give.length>0){st.hands[t]=st.hands[t].filter(c=>c.slice(0,-1)!==r);st.hands[idx]=st.hands[idx].concat(give);gotN=give.length;newB=game._books(st.hands,st.books,idx);msg='got '+gotN+' '+r;extra=true;}
else{if(st.deck.length>0){drew=st.deck.pop();st.hands[idx]=st.hands[idx].concat([drew]);const nb=game._books(st.hands,st.books,idx);newB=nb;if(drew.slice(0,-1)===r){msg='fished the '+r;extra=true;}else{msg='go fish';extra=false;}}else{msg='go fish (empty)';extra=false;}}
const entry={kind:'ask',from:idx,to:t,rank:r,got:gotN,fished:give.length===0,drew,extra,newB,msg};
st.last=entry;
const an=ctx.players[idx]?ctx.players[idx].display_name:'?';const tn2=ctx.players[t]?ctx.players[t].display_name:'?';
if(gotN>0)st.log.push(an+' got '+gotN+' '+r+' from '+tn2);else st.log.push(an+' asked '+tn2+' for '+r+'s — Go Fish!');
if(newB.length)st.log.push('\uD83D\uDCCA Book(s): '+newB.join(','));
while(st.log.length>30)st.log.shift();
game._refill(st);
if(game._isOver(st)){st.winners=game._winners(st);return{state:st,turn:null,payouts:game._payouts(st,ctx)};}
let nx;
if(extra){nx=idx;if(st.hands[nx].length===0){if(st.deck.length===0)nx=game._next(st,idx);}}
else nx=game._next(st,idx);
if(st.deck.length===0){let g=0;while(st.hands[nx].length===0&&g<10){nx=(nx+1)%st.hands.length;g++;}if(st.hands[nx].length===0&&game._isOver(st)){st.winners=game._winners(st);return{state:st,turn:null,payouts:game._payouts(st,ctx)};}}
return{state:st,turn:[idOf(nx)]};},
computerMove(state,playerId,ctx){return game.randomMove(state,playerId,ctx);},
randomMove(state,playerId,ctx){const idx=ctx.players.findIndex(p=>p.id===playerId);const h=state.hands[idx]||[];
if(h.length===0){if(state.deck.length>0)return{draw:true};return{pass:true};}
const set=[];h.forEach(c=>{const r=c.slice(0,-1);if(set.indexOf(r)<0)set.push(r);});
const rank=set[Math.floor(ctx.random()*set.length)];
let cands=[];for(let i=0;i<state.hands.length;i++){if(i!==idx&&state.hands[i].length>0)cands.push(i);}
if(cands.length===0)for(let i=0;i<state.hands.length;i++)if(i!==idx)cands.push(i);
const tgt=cands[Math.floor(ctx.random()*cands.length)];
return{target:tgt,rank};},
viewFor(state,playerId,ctx){if(!state||!state.deck)return null;const idx=playerId==null?-1:ctx.players.findIndex(p=>p.id===playerId);
return{deckCount:state.deck.length,counts:state.hands.map(h=>h.length),books:state.books.map(b=>b.slice()),hand:idx>=0?state.hands[idx].slice():null,log:state.log.slice(),last:state.last?Object.assign({},state.last,{drew:idx>=0&&idx===state.last.from?state.last.drew:null}):null,winners:state.winners};}};