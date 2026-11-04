const game = {
  FLEET: [{name:"Carrier",size:5},{name:"Battleship",size:4},{name:"Cruiser",size:3},{name:"Submarine",size:3},{name:"Destroyer",size:2}],
  emptyAtk: function(){ const a=[]; for(let y=0;y<10;y++){a.push([0,0,0,0,0,0,0,0,0,0]);} return a; },
  setupIdx: function(state){ if(!state.boards[0].ready) return 0; if(!state.boards[1].ready) return 1; return -1; },
  idxOf: function(state, playerId, ctx){ for(let i=0;i<ctx.players.length;i++) if(ctx.players[i].id===playerId) return i; return -1; },
  validShips: function(ships){
    const F=this.FLEET;
    if(!Array.isArray(ships)||ships.length!==5) return "need 5 ships";
    const byName=Object.create(null);
    for(const s of ships){ if(!s||typeof s.name!=="string"||!Array.isArray(s.cells)) return "bad ship"; byName[s.name]=s; }
    for(const f of F){ const s=byName[f.name]; if(!s) return "missing "+f.name; if(s.cells.length!==f.size) return f.name+" wrong size"; }
    const occ=new Set();
    for(const s of ships){ for(const c of s.cells){ if(!Array.isArray(c)||c.length!==2) return "bad cell"; const x=c[0],y=c[1]; if(!Number.isInteger(x)||!Number.isInteger(y)||x<0||x>9||y<0||y>9) return "off board"; const k=x+","+y; if(occ.has(k)) return "overlap"; occ.add(k); } }
    const occ2=new Set(occ);
    for(const s of ships){ for(const c of s.cells){ occ2.delete(c[0]+","+c[1]); } }
    // check straight + contiguous + no-touch using full occupancy
    for(const s of ships){
      const cells=s.cells.slice().sort((a,b)=>(a[1]-b[1])||(a[0]-b[0]));
      const sameX=cells.every(c=>c[0]===cells[0][0]), sameY=cells.every(c=>c[1]===cells[0][1]);
      if(!sameX&&!sameY) return s.name+" must be straight";
      for(let i=1;i<cells.length;i++){ const dx=Math.abs(cells[i][0]-cells[i-1][0]), dy=Math.abs(cells[i][1]-cells[i-1][1]); if(dx+dy!==1) return s.name+" must be contiguous"; }
    }
    const full=new Set(); ships.forEach(s=>s.cells.forEach(c=>full.add(c[0]+","+c[1])));
    for(const s of ships){ for(const c of s.cells){ for(let dy=-1;dy<=1;dy++) for(let dx=-1;dx<=1;dx++){ if(!dx&&!dy) continue; const k=(c[0]+dx)+","+(c[1]+dy); if(full.has(k)){ let own=false; for(const cc of s.cells){ if(cc[0]===c[0]+dx&&cc[1]===c[1]+dy){own=true;break;} } if(!own) return "ships can't touch"; } } } }
    return null;
  },
  isSunk: function(ship, atk){ if(!ship.cells||!ship.cells.length) return false; for(let i=0;i<ship.cells.length;i++){ const c=ship.cells[i]; if(atk[c[1]][c[0]]!==2) return false; } return true; },
  allSunk: function(ships, atk){ if(!ships||ships.length!==5) return false; for(let i=0;i<ships.length;i++) if(!this.isSunk(ships[i],atk)) return false; return true; },
  payoutsFor: function(winnerIdx, ctx){
    const pot=ctx.pot||0;
    const humans=ctx.players.filter(p=>p.kind==="human");
    if(humans.length===0) return {};
    const m={}; humans.forEach(h=>{m[h.id]=0;});
    if(winnerIdx===0||winnerIdx===1){
      const w=ctx.players[winnerIdx];
      if(w&&w.kind==="human"){ m[w.id]=pot; return m; }
    }
    const share=Math.floor(pot/humans.length); let rem=pot-share*humans.length;
    humans.forEach(h=>{ m[h.id]=share+(rem>0?1:0); if(rem>0) rem--; });
    return m;
  },
  newMatch: function(ctx){
    const state={phase:"SETUP",boards:[{ships:[],ready:false},{ships:[],ready:false}],attacks:[this.emptyAtk(),this.emptyAtk()],turnIdx:0,winnerIdx:null,last:null,moves:0};
    return {state:state, turn:[ctx.players[0].id]};
  },
  applyMove: function(state, playerId, move, ctx){
    const idx=this.idxOf(state,playerId,ctx);
    if(idx!==0&&idx!==1) throw new InvalidMove("unknown player");
    if(state.winnerIdx===0||state.winnerIdx===1) throw new InvalidMove("game over");
    if(!move||typeof move.type!=="string") throw new InvalidMove("bad move");
    if(state.phase==="SETUP"){
      if(move.type!=="place") throw new InvalidMove("place your ships");
      const sp=this.setupIdx(state);
      if(sp<0) throw new InvalidMove("setup done");
      if(idx!==sp) throw new InvalidMove("not your setup turn");
      const err=this.validShips(move.ships);
      if(err) throw new InvalidMove(err);
      const ships=move.ships.map(s=>({name:s.name,size:this.FLEET.find(f=>f.name===s.name).size,cells:s.cells.map(c=>[c[0],c[1]])}));
      state.boards[idx].ships=ships;
      state.boards[idx].ready=true;
      state.moves++;
      if(state.boards[0].ready&&state.boards[1].ready){
        state.phase="BATTLE"; state.turnIdx=0; state.last={msg:"Battle start! P1 fires. HIT keeps your turn!"};
        return {state:state, turn:[ctx.players[0].id]};
      }
      const nx=this.setupIdx(state);
      return {state:state, turn:[ctx.players[nx].id]};
    }
    if(state.phase==="BATTLE"){
      if(move.type!=="fire") throw new InvalidMove("fire with x,y");
      if(idx!==state.turnIdx) throw new InvalidMove("not your turn");
      const x=move.x, y=move.y;
      if(!Number.isInteger(x)||!Number.isInteger(y)||x<0||x>9||y<0||y>9) throw new InvalidMove("off board");
      const opp=1-idx;
      if(state.attacks[opp][y][x]!==0) throw new InvalidMove("already shot there");
      let hit=false;
      for(const s of state.boards[opp].ships){ for(const c of s.cells){ if(c[0]===x&&c[1]===y){hit=true;break;} } if(hit) break; }
      state.attacks[opp][y][x]=hit?2:1;
      state.moves++;
      let sunkName=null;
      if(hit){ for(const s of state.boards[opp].ships){ let on=false; for(const c of s.cells){ if(c[0]===x&&c[1]===y){on=true;break;} } if(on){ if(this.isSunk(s,state.attacks[opp])) sunkName=s.name; break; } } }
      if(hit&&this.allSunk(state.boards[opp].ships,state.attacks[opp])){
        state.winnerIdx=idx; state.turnIdx=idx; state.last={by:idx,x:x,y:y,hit:true,sunk:sunkName,gameOver:true};
        return {state:state, turn:null, payouts:this.payoutsFor(idx,ctx)};
      }
      if(state.moves>500){
        let h0=0,h1=0;
        state.attacks[0].forEach(r=>r.forEach(c=>{if(c===2)h0++;}));
        state.attacks[1].forEach(r=>r.forEach(c=>{if(c===2)h1++;}));
        let w=h0===h1?idx:(h1>h0?1:0);
        // h1 = hits on P1 = scored by P0; h0 scored by P1. winner is higher scorer
        w=(h1>=h0)?0:1; if(h0===h1) w=idx;
        state.winnerIdx=w; state.turnIdx=w; state.last={by:idx,x:x,y:y,hit:hit,sunk:sunkName,gameOver:true,cap:true};
        return {state:state, turn:null, payouts:this.payoutsFor(w,ctx)};
      }
      if(hit){ state.last={by:idx,x:x,y:y,hit:true,sunk:sunkName,extra:true}; state.turnIdx=idx; return {state:state, turn:[ctx.players[idx].id]}; }
      state.last={by:idx,x:x,y:y,hit:false,sunk:null,extra:false}; state.turnIdx=opp; return {state:state, turn:[ctx.players[opp].id]};
    }
    throw new InvalidMove("bad phase");
  },
  genFleet: function(rand){
    const F=this.FLEET;
    const cellsFor=(sx,sy,size,h)=>{const c=[];for(let i=0;i<size;i++)c.push([h?sx+i:sx,h?sy:sy+i]);return c;};
    for(let r=0;r<30;r++){
      const ships=[]; const occ=new Set();
      const ok=(cells)=>{ for(const cc of cells){ const x=cc[0],y=cc[1]; if(x<0||x>9||y<0||y>9) return false; if(occ.has(x+","+y)) return false; for(let dy=-1;dy<=1;dy++) for(let dx=-1;dx<=1;dx++){ if(!dx&&!dy) continue; if(occ.has((x+dx)+","+(y+dy))) return false; } } return true; };
      let fail=false;
      for(const f of F){
        let placed=false;
        for(let t=0;t<60&&!placed;t++){ const h=rand()<0.5; const x=Math.floor(rand()*10), y=Math.floor(rand()*10); const cells=cellsFor(x,y,f.size,h); if(ok(cells)){ ships.push({name:f.name,size:f.size,cells:cells}); cells.forEach(c=>occ.add(c[0]+","+c[1])); placed=true; } }
        if(!placed){fail=true;break;}
      }
      if(!fail&&ships.length===5) return ships;
    }
    return [{name:"Carrier",size:5,cells:cellsFor(0,0,5,true)},{name:"Battleship",size:4,cells:cellsFor(0,2,4,true)},{name:"Cruiser",size:3,cells:cellsFor(0,4,3,true)},{name:"Submarine",size:3,cells:cellsFor(0,6,3,true)},{name:"Destroyer",size:2,cells:cellsFor(0,8,2,true)}];
  },
  pickShot: function(state, idx, rand, smart){
    const opp=1-idx; const atk=state.attacks[opp]; const ships=state.boards[opp].ships;
    const sunk=new Set();
    ships.forEach(sh=>{ let all=sh.cells.length>0; for(const c of sh.cells){ if(atk[c[1]][c[0]]!==2){all=false;break;} } if(all) sh.cells.forEach(c=>sunk.add(c[0]+","+c[1])); });
    if(smart){
      const hits=[];
      for(let y=0;y<10;y++) for(let x=0;x<10;x++) if(atk[y][x]===2&&!sunk.has(x+","+y)) hits.push([x,y]);
      const cand=[]; const seen=new Set();
      const add=(x,y)=>{ if(x>=0&&x<10&&y>=0&&y<10&&atk[y][x]===0){ const k=x+","+y; if(!seen.has(k)){seen.add(k);cand.push([x,y]);} } };
      if(hits.length>=2){
        const sameR=hits.every(c=>c[1]===hits[0][1]), sameC=hits.every(c=>c[0]===hits[0][0]);
        if(sameR||sameC){
          const s=hits.slice().sort((a,b)=>sameR?a[0]-b[0]:a[1]-b[1]);
          const ends=[s[0],s[s.length-1]];
          for(const e of ends){ if(sameR){add(e[0]+1,e[1]);add(e[0]-1,e[1]);} else {add(e[0],e[1]+1);add(e[0],e[1]-1);} }
          if(cand.length) return cand[Math.floor(rand()*cand.length)];
        }
      }
      for(const h of hits){ add(h[0]+1,h[1]);add(h[0]-1,h[1]);add(h[0],h[1]+1);add(h[0],h[1]-1); }
      if(cand.length) return cand[Math.floor(rand()*cand.length)];
    }
    const pool=[];
    for(let y=0;y<10;y++) for(let x=0;x<10;x++) if(atk[y][x]===0) pool.push([x,y]);
    if(!pool.length) return [0,0];
    return pool[Math.floor(rand()*pool.length)];
  },
  computerMove: function(state, playerId, ctx){
    const idx=this.idxOf(state,playerId,ctx);
    const rand=ctx.random;
    if(state.phase==="SETUP") return {type:"place",ships:this.genFleet(rand)};
    const p=this.pickShot(state,idx,rand,true);
    return {type:"fire",x:p[0],y:p[1]};
  },
  randomMove: function(state, playerId, ctx){
    const idx=this.idxOf(state,playerId,ctx);
    const rand=ctx.random;
    if(state.phase==="SETUP") return {type:"place",ships:this.genFleet(rand)};
    const p=this.pickShot(state,idx,rand,false);
    return {type:"fire",x:p[0],y:p[1]};
  },
  viewFor: function(state, playerId, ctx){
    let me=-1;
    if(playerId!==null&&playerId!==undefined){ for(let i=0;i<ctx.players.length;i++) if(ctx.players[i].id===playerId){me=i;break;} }
    const atkCopy=[this.emptyAtk(),this.emptyAtk()];
    for(let b=0;b<2;b++) for(let y=0;y<10;y++) for(let x=0;x<10;x++) atkCopy[b][y][x]=state.attacks[b][y][x];
    const filt=(bi)=>{
      const sh=state.boards[bi].ships||[];
      if(me===bi||(state.winnerIdx===0||state.winnerIdx===1)) return sh.map(s=>({name:s.name,size:s.size,cells:s.cells.map(c=>[c[0],c[1]])}));
      const atk=state.attacks[bi];
      const out=[];
      for(const s of sh){ let all=s.cells.length>0; for(const c of s.cells){ if(atk[c[1]][c[0]]!==2){all=false;break;} } if(all) out.push({name:s.name,size:s.size,cells:s.cells.map(c=>[c[0],c[1]])}); }
      return out;
    };
    return {phase:state.phase,turnIdx:state.turnIdx,winnerIdx:state.winnerIdx,last:state.last?JSON.parse(JSON.stringify(state.last)):null,moves:state.moves,boards:[{ships:filt(0),ready:!!state.boards[0].ready},{ships:filt(1),ready:!!state.boards[1].ready}],attacks:atkCopy};
  }
};