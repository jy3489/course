const game = {
  rank(c){return c%13+1;},
  suit(c){return Math.floor(c/13);},
  playable(state,seat){
    const top=state.discard[state.discard.length-1];
    const tr=top%13+1, tsuit=Math.floor(top/13);
    return state.hands[seat].filter(c=>{const r=c%13+1,s=Math.floor(c/13);return r===8||s===state.suit||r===tr;});
  },
  points(hand){return hand.reduce((n,c)=>{const r=c%13+1;return n+(r===8?50:Math.min(r,10));},0);},
  payoutsFor(state,ctx){
    const humans=ctx.players.filter(p=>p.kind==='human');
    const out={};
    if(humans.length===0||ctx.pot<=0)return out;
    const ids=humans.map(p=>p.id);
    if(state.winner!==null){
      const wId=ctx.players[state.winner]?ctx.players[state.winner].id:null;
      const wIsHuman=wId&&ids.includes(wId);
      if(wIsHuman){ids.forEach(id=>out[id]=0);out[wId]=ctx.pot;return out;}
    }
    const base=Math.floor(ctx.pot/ids.length);let rem=ctx.pot-base*ids.length;
    ids.forEach(id=>{out[id]=base+(rem>0?1:0);if(rem>0)rem--;});
    return out;
  },
  newMatch(ctx){
    const n=ctx.players.length;
    const deck=Array.from({length:52},(_,i)=>i);
    for(let i=51;i>0;i--){const j=Math.floor(ctx.random()*(i+1));const t=deck[i];deck[i]=deck[j];deck[j]=t;}
    const hands=Array.from({length:n},()=>deck.splice(0,5));
    let k=deck.findIndex(c=>(c%13+1)!==8);
    if(k<0)k=0;
    const top=deck.splice(k,1)[0];
    const state={hands,deck,discard:[top],suit:Math.floor(top/13),turnIdx:0,done:false,winner:null,passes:0};
    return {state,turn:[ctx.players[0].id]};
  },
  applyMove(state,playerId,move,ctx){
    if(state.done)throw new InvalidMove('Match is over');
    const seat=state.turnIdx;
    if(!ctx.players[seat]||ctx.players[seat].id!==playerId)throw new InvalidMove('Not your turn');
    state=JSON.parse(JSON.stringify(state));
    const n=state.hands.length;
    if(move==='draw'){
      if(state.deck.length===0&&state.discard.length>1){const top=state.discard.pop();state.deck=state.discard.reverse();state.discard=[top];}
      if(state.deck.length){state.hands[seat].push(state.deck.pop());state.passes=0;}
      else{
        state.passes++;
        if(state.passes>=n){
          state.done=true;
          const pts=state.hands.map(h=>game.points(h));
          const mn=Math.min(...pts);
          state.winner=pts.filter(x=>x===mn).length===1?pts.indexOf(mn):null;
        }
      }
    } else if(move&&typeof move==='object'&&typeof move.card==='number'){
      const card=move.card;
      const legal=game.playable(state,seat);
      if(!legal.includes(card))throw new InvalidMove('Illegal card');
      const r=card%13+1;
      if(r===8){
        if(![0,1,2,3].includes(move.chosenSuit))throw new InvalidMove('Choose a suit');
      }
      const idx=state.hands[seat].indexOf(card);
      if(idx<0)throw new InvalidMove('Card not in hand');
      state.hands[seat].splice(idx,1);
      state.discard.push(card);
      state.suit=r===8?move.chosenSuit:Math.floor(card/13);
      state.passes=0;
      if(state.hands[seat].length===0){state.done=true;state.winner=seat;}
    } else throw new InvalidMove('Unknown move');
    if(state.done)return {state,turn:null,payouts:game.payoutsFor(state,ctx)};
    state.turnIdx=(seat+1)%n;
    return {state,turn:[ctx.players[state.turnIdx].id]};
  },
  computerMove(state,playerId,ctx){
    const seat=ctx.players.findIndex(p=>p.id===playerId);
    const cards=game.playable(state,seat);
    if(!cards.length)return 'draw';
    const card=cards.find(c=>(c%13+1)!==8)??cards[0];
    if((card%13+1)!==8)return {card};
    const counts=[0,1,2,3].map(s=>state.hands[seat].filter(c=>Math.floor(c/13)===s&&(c%13+1)!==8).length);
    return {card,chosenSuit:counts.indexOf(Math.max(...counts))};
  },
  randomMove(state,playerId,ctx){
    const seat=ctx.players.findIndex(p=>p.id===playerId);
    const cards=game.playable(state,seat);
    if(!cards.length)return 'draw';
    const card=cards[Math.floor(ctx.random()*cards.length)];
    if((card%13+1)!==8)return {card};
    return {card,chosenSuit:Math.floor(ctx.random()*4)};
  },
  viewFor(state,playerId,ctx){
    const idx=playerId?ctx.players.findIndex(p=>p.id===playerId):-1;
    const counts=state.hands.map(h=>h.length);
    return {
      hand:idx>=0?state.hands[idx].slice():null,
      counts,deckCount:state.deck.length,
      top:state.discard[state.discard.length-1],
      suit:state.suit,turnIdx:state.turnIdx,
      done:state.done,winner:state.winner,
      playable:idx>=0?game.playable(state,idx):[]
    };
  }
};