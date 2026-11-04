const game={
newMatch(ctx){
 let d=[];for(let r=0;r<13;r++)for(let s=0;s<4;s++)d.push({r,s});d.push({r:13,s:0});d.push({r:14,s:0});
 for(let i=d.length-1;i>0;i--){let j=Math.floor(ctx.random()*(i+1));let t=d[i];d[i]=d[j];d[j]=t}
 let hands=[[],[],[]];for(let i=0;i<51;i++)hands[i%3].push(d[i]);let bot=d.slice(51);
 let sh=h=>h.sort((a,b)=>b.r-a.r||b.s-a.s);hands.forEach(sh);sh(bot);
 let state={phase:'bid',hands,bot,bids:[null,null,null],bidTurn:0,highBid:0,landlord:-1,turn:-1,last:null,passes:0,winner:-1};
 return {state,turn:[pid(ctx,0)]};
},
applyMove(state,playerId,move,ctx){
 let seat=seatOf(ctx,playerId);if(seat<0)throw new InvalidMove('not seated');
 move=move||{};
 if(state.phase==='bid'){
  if(seat!==state.bidTurn)throw new InvalidMove("not your bid turn");
  if(move.kind!=='bid')throw new InvalidMove('send bid');
  let b=move.bid;if(b!==0&&b!==1&&b!==2&&b!==3)throw new InvalidMove('bid 0-3');
  if(b>0&&b<=state.highBid)throw new InvalidMove('must top '+state.highBid);
  state.bids[seat]=b;if(b>state.highBid)state.highBid=b;
  if(b===3){finishBid(state);return endOr(state,ctx)}
  state.bidTurn=(state.bidTurn+1)%3;
  if(state.bids.every(x=>x!==null)){finishBid(state);return endOr(state,ctx)}
  return {state,turn:[pid(ctx,state.bidTurn)]};
 }
 if(state.phase==='play'){
  if(seat!==state.turn)throw new InvalidMove('not your turn');
  if(move.kind==='pass'){
   if(!state.last||state.last.player===seat)throw new InvalidMove('you lead - must play');
   state.passes++;state.turn=(seat+1)%3;
   if(state.last&&state.passes>=2){state.last=null;state.passes=0}
   return {state,turn:[pid(ctx,state.turn)]};
  }
  if(move.kind!=='play')throw new InvalidMove('send play or pass');
  let cards=move.cards;if(!Array.isArray(cards)||!cards.length)throw new InvalidMove('select cards');
  let tmp=state.hands[seat].slice();
  for(let c of cards){if(!c||typeof c.r!=='number'||typeof c.s!=='number')throw new InvalidMove('bad card');let i=tmp.findIndex(x=>x.r===c.r&&x.s===c.s);if(i<0)throw new InvalidMove('card not in hand');tmp.splice(i,1)}
  let c=combo(cards);if(!c)throw new InvalidMove('invalid combo');
  if(state.last&&state.last.player!==seat&&!beats(c,state.last.combo))throw new InvalidMove('must beat '+state.last.combo.label);
  state.hands[seat]=tmp;
  state.last={player:seat,cards:cards.map(x=>({r:x.r,s:x.s})),combo:c};
  state.passes=0;state.turn=(seat+1)%3;
  if(state.hands[seat].length===0){state.phase='over';state.winner=seat;state.turn=-1;
   return {state,turn:null,payouts:payoutsFor(state,ctx)}}
  return {state,turn:[pid(ctx,state.turn)]};
 }
 throw new InvalidMove('game over');
},
computerMove(state,playerId,ctx){return aiMove(state,playerId,ctx,false)},
randomMove(state,playerId,ctx){return aiMove(state,playerId,ctx,true)},
viewFor(state,playerId,ctx){
 let idx=playerId?seatOf(ctx,playerId):-1;
 let counts=state.hands.map(h=>h.length);
 let cur=state.phase==='bid'?state.bidTurn:state.turn;
 let v={phase:state.phase,bot:state.bot.map(c=>({r:c.r,s:c.s})),bids:state.bids.slice(),bidTurn:state.bidTurn,highBid:state.highBid,landlord:state.landlord,curSeat:cur,turnSeat:cur,passes:state.passes,winner:state.winner,handCounts:counts,hand:null,last:null};
 if(state.last)v.last={player:state.last.player,cards:state.last.cards.map(c=>({r:c.r,s:c.s})),combo:{type:state.last.combo.type,len:state.last.combo.len,rank:state.last.combo.rank,label:state.last.combo.label}};
 if(idx>=0)v.hand=state.hands[idx].map(c=>({r:c.r,s:c.s}));
 return v;
}
};
function pid(ctx,i){let p=ctx.players[i];if(!p)return 'p'+i;if(typeof p==='string')return p;return p.id||('p'+i)}
function seatOf(ctx,playerId){for(let i=0;i<ctx.players.length;i++){let p=ctx.players[i];let id=(typeof p==='string')?p:(p&&p.id);if(id===playerId)return i}return -1}
function endOr(state,ctx){if(state.phase==='over'){return {state,turn:null,payouts:payoutsFor(state,ctx)}}let t=state.phase==='bid'?state.bidTurn:state.turn;return {state,turn:[pid(ctx,t)]}}
function finishBid(st){let best=-1,bv=0;for(let i=0;i<3;i++)if(st.bids[i]>bv){bv=st.bids[i];best=i}if(best===-1){best=0;bv=1}st.landlord=best;st.highBid=bv;st.hands[best]=st.hands[best].concat(st.bot);st.hands[best].sort((a,b)=>b.r-a.r||b.s-a.s);st.phase='play';st.turn=best;st.last=null;st.passes=0}
function payoutsFor(state,ctx){
 let pot=ctx.pot||0;let p={};if(pot<=0)return p;
 let humans=ctx.players.filter(pl=>{let k=(typeof pl==='string')?null:pl.kind;return k!=='computer'});if(!humans.length)return p;
 let hid=h=> (typeof h==='string')?h:(h.id);
 let team=state.winner===state.landlord?[state.landlord]:[0,1,2].filter(i=>i!==state.landlord);
 let hw=team.map(i=>ctx.players[i]).filter(pl=>pl&&((typeof pl==='string')||pl.kind!=='computer'));
 let rec=hw.length?hw:humans;
 let each=Math.floor(pot/rec.length),rem=pot-each*rec.length;
 rec.forEach((pl,i)=>{p[hid(pl)]=each+(i<rem?1:0)});
 return p;
}
function countsOf(h){let m={};h.forEach(c=>m[c.r]=(m[c.r]||0)+1);return m}
function byRank(h){let m={};h.forEach(c=>{(m[c.r]=m[c.r]||[]).push(c)});return m}
function combo(cards){
 if(!cards||!cards.length)return null;let n=cards.length,m=countsOf(cards),ks=Object.keys(m).map(Number).sort((a,b)=>a-b),vals=ks.map(k=>m[k]).sort((a,b)=>a-b);
 if(n===2&&m[13]===1&&m[14]===1)return{type:'rocket',len:2,rank:99,label:'\uD83D\uDE80 Rocket'};
 if(n===1)return{type:'single',len:1,rank:cards[0].r,label:'Single'};
 if(n===2&&ks.length===1)return{type:'pair',len:2,rank:ks[0],label:'Pair'};
 if(n===3&&ks.length===1)return{type:'trio',len:3,rank:ks[0],label:'Trio'};
 if(n===4&&ks.length===1)return{type:'bomb',len:4,rank:ks[0],label:'\uD83D\uDCA3 Bomb'};
 if(n===4&&ks.length===2&&vals.join()==='1,3'){let t=ks.find(k=>m[k]===3);return{type:'trio1',len:4,rank:t,label:'Trio+1'}}
 if(n===5&&ks.length===2&&vals.join()==='2,3'){let t=ks.find(k=>m[k]===3);return{type:'trio2',len:5,rank:t,label:'Trio+Pair'}}
 if(n>=5&&n<=12&&ks.length===n&&ks.every(k=>k<12)&&ks[n-1]-ks[0]===n-1)return{type:'straight',len:n,rank:ks[n-1],label:'Straight '+n};
 if(n>=6&&n%2===0&&n<=20&&ks.length===n/2&&ks.every(k=>k<12)&&ks[ks.length-1]-ks[0]===ks.length-1&&vals.every(v=>v===2))return{type:'pairseq',len:n,rank:ks[ks.length-1],label:'Pairs '+n/2};
 if(n>=6&&n%3===0){let k=n/3;if(k>=2&&ks.length===k&&ks.every(kk=>kk<12)&&ks[k-1]-ks[0]===k-1&&vals.every(v=>v===3))return{type:'air',len:n,rank:ks[k-1],k,label:'\u2708\uFE0F Plane'}}
 if(n>=8&&n%4===0){let k=n/4;if(k>=2){let trios=ks.filter(kk=>m[kk]>=3&&kk<12).sort((a,b)=>a-b);for(let i=0;i+k-1<trios.length;i++){let sq=trios.slice(i,i+k);if(sq[k-1]-sq[0]===k-1){let rem=n-k*3,av=0;ks.forEach(kk=>{let l=m[kk]-(sq.includes(kk)?3:0);if(l>0)av+=l});if(av===rem||rem===k)return{type:'air1',len:n,rank:sq[k-1],k,sq,label:'\u2708\uFE0F+singles'}}}}}
 if(n>=10&&n%5===0){let k=n/5;if(k>=2){let trios=ks.filter(kk=>m[kk]>=3&&kk<12).sort((a,b)=>a-b);for(let i=0;i+k-1<trios.length;i++){let sq=trios.slice(i,i+k);if(sq[k-1]-sq[0]===k-1){let pairs=ks.filter(kk=>!sq.includes(kk)&&m[kk]>=2);if(pairs.length>=k)return{type:'air2',len:n,rank:sq[k-1],k,sq,label:'\u2708\uFE0F+pairs'}}}}}
 if(n===6){let q=ks.find(k=>m[k]===4);if(q!==undefined)return{type:'four2',len:6,rank:q,label:'Four+2'}}
 if(n===8){let q=ks.find(k=>m[k]===4);if(q!==undefined){let pr=ks.filter(k=>k!==q&&m[k]>=2);if(pr.length>=2)return{type:'four2p',len:8,rank:q,label:'Four+2pairs'}}}
 return null;
}
function beats(a,b){if(!b)return true;if(a.type==='rocket')return true;if(b.type==='rocket')return false;if(a.type==='bomb'&&b.type!=='bomb')return true;if(b.type==='bomb'&&a.type!=='bomb')return false;if(a.type!==b.type)return false;if(a.len!==b.len)return false;return a.rank>b.rank}
function lowestOf(h,r,need){let br=byRank(h);if((br[r]||[]).length>=need)return br[r].slice(0,need);return null}
function findBeat(h,last){
 let br=byRank(h),ranks=Object.keys(br).map(Number).sort((a,b)=>a-b);
 function bombs(minR){let o=[];ranks.forEach(r=>{if(r<13&&br[r].length===4&&(minR==null||r>minR))o.push(br[r].slice())});if(br[13]&&br[14])o.push([br[13][0],br[14][0]]);return o}
 if(!last)return lowestOf(h,ranks[0],1);
 let t=last.combo.type,L=last.combo.len,R=last.combo.rank;
 function fh(need){for(let r of ranks){if(r>R&&br[r].length>=need)return br[r].slice(0,need)}return null}
 if(t==='single')return fh(1)||bombs(null)[0]||null;
 if(t==='pair')return fh(2)||bombs(null)[0]||null;
 if(t==='trio')return fh(3)||bombs(null)[0]||null;
 if(t==='trio1'){for(let r of ranks){if(r>R&&br[r].length>=3){let k=h.find(c=>c.r!==r);if(k)return br[r].slice(0,3).concat([k])}}return bombs(null)[0]||null}
 if(t==='trio2'){for(let r of ranks){if(r>R&&br[r].length>=3){let pk=ranks.find(k=>k!==r&&br[k].length>=2);if(pk!==undefined)return br[r].slice(0,3).concat(br[pk].slice(0,2))}}return bombs(null)[0]||null}
 if(t==='bomb')return bombs(R)[0]||null;
 if(t==='rocket')return null;
 if(t==='straight'){for(let hi=R+1;hi<12;hi++){let lo=hi-L+1;if(lo<0)continue;let sq=[];for(let r=lo;r<=hi;r++)sq.push(r);if(sq.every(r=>br[r]&&br[r].length>=1))return sq.map(r=>br[r][0])}return bombs(null)[0]||null}
 if(t==='pairseq'){let k=L/2;for(let hi=R+1;hi<12;hi++){let lo=hi-k+1;if(lo<0)continue;let sq=[];for(let r=lo;r<=hi;r++)sq.push(r);if(sq.every(r=>br[r]&&br[r].length>=2))return sq.flatMap(r=>br[r].slice(0,2))}return bombs(null)[0]||null}
 if(t==='air'){let k=last.combo.k;for(let hi=R+1;hi<12;hi++){let lo=hi-k+1;if(lo<0)continue;let sq=[];for(let r=lo;r<=hi;r++)sq.push(r);if(sq.every(r=>br[r]&&br[r].length>=3))return sq.flatMap(r=>br[r].slice(0,3))}return bombs(null)[0]||null}
 if(t==='air1'){let k=last.combo.k;for(let hi=R+1;hi<12;hi++){let lo=hi-k+1;if(lo<0)continue;let sq=[];for(let r=lo;r<=hi;r++)sq.push(r);if(sq.every(r=>br[r]&&br[r].length>=3)){let core=seqFlat(br,sq);let rest=h.filter(c=>core.indexOf(c)<0).slice(0,k);if(rest.length===k)return core.concat(rest)}}return bombs(null)[0]||null}
 if(t==='air2'){let k=last.combo.k;for(let hi=R+1;hi<12;hi++){let lo=hi-k+1;if(lo<0)continue;let sq=[];for(let r=lo;r<=hi;r++)sq.push(r);if(sq.every(r=>br[r]&&br[r].length>=3)){let core=seqFlat(br,sq);let pr=ranks.filter(r=>sq.indexOf(r)<0&&br[r].length>=2);if(pr.length>=k){let ex=[];pr.slice(0,k).forEach(r=>ex.push(br[r][0],br[r][1]));return core.concat(ex)}}}return bombs(null)[0]||null}
 if(t==='four2'){for(let r of ranks){if(r>R&&br[r].length===4){let rest=h.filter(c=>c.r!==r).slice(0,2);if(rest.length===2)return br[r].slice().concat(rest)}}return bombs(null)[0]||null}
 if(t==='four2p'){for(let r of ranks){if(r>R&&br[r].length===4){let pr=ranks.filter(k=>k!==r&&br[k].length>=2);if(pr.length>=2){let ex=[];pr.slice(0,2).forEach(k=>ex.push(br[k][0],br[k][1]));return br[r].slice().concat(ex)}}}return bombs(null)[0]||null}
 return bombs(null)[0]||null;
}
function seqFlat(br,sq){let o=[];sq.forEach(r=>{o.push(br[r][0],br[r][1],br[r][2])});return o}
function suggestLead(h){let br=byRank(h),ranks=Object.keys(br).map(Number).sort((a,b)=>a-b);
 for(let r of ranks)if(br[r].length===1)return[br[r][0]];
 for(let r of ranks)if(br[r].length===2)return br[r].slice();
 for(let r of ranks)if(br[r].length===3){let k=h.find(c=>c.r!==r);if(k&&h.length>4)return[br[r][0]];return br[r].slice()}
 return [br[ranks[0]][0]];
}
function aiMove(state,playerId,ctx,randomish){
 let seat=seatOf(ctx,playerId);
 if(seat<0)return{kind:'pass'};
 if(state.phase==='bid'){
  if(randomish){let opts=[0];for(let b=1;b<=3;b++)if(b>state.highBid)opts.push(b);return{kind:'bid',bid:opts[Math.floor(ctx.random()*opts.length)]}}
  let h=state.hands[seat],pow=h.filter(c=>c.r>=12).length;
  let want=0;if(state.highBid<3){if(pow>=6)want=3;else if(pow>=4&&state.highBid<2)want=2;else if(pow>=3&&state.highBid<1)want=1;if(want<=state.highBid)want=0;if(ctx.random()<0.15&&want===0&&state.highBid<1)want=1}
  return{kind:'bid',bid:Math.min(3,want)};
 }
 if(state.phase==='play'){
  let h=state.hands[seat];
  if(seat!==state.turn)return{kind:'pass'};
  if(!state.last||state.last.player===seat)return{kind:'play',cards:suggestLead(h).map(c=>({r:c.r,s:c.s}))};
  let mv=findBeat(h,state.last);
  if(randomish&&mv&&ctx.random()<0.25)mv=null;
  if(!mv)return{kind:'pass'};
  return{kind:'play',cards:mv.map(c=>({r:c.r,s:c.s}))};
 }
 return{kind:'pass'};
}