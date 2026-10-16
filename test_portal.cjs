const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup(request=async()=>{}) {
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id)) nodes.set(id,{value:'',hidden:false,disabled:false,dataset:{},children:[],
      replaceChildren(...children){this.children=children;},setAttribute(){},addEventListener(){},focus(){}});
    return nodes.get(id);
  };
  const ctx=vm.createContext({console,Map,Set,URLSearchParams,
    navigator:{onLine:true},localStorage:{},PortalOffline:class {api(...args){return request(...args);}read(){return {};}write(){}},
    document:{getElementById:node},window:{addEventListener(){}},
    location:{hash:''},history:{replaceState(){}},
    Auth:{current:()=>({id:'owner'})},GameFrame:class{load(){}unload(){}send(){}},
    apiRequest:request,formatTime:()=>'',shortId:x=>x,showPageError(){},el:(tag,attrs={},...children)=>({...attrs,children})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'static/portal.js'),'utf8').replace(/main\(\);\s*$/,''),ctx);
  const run=code=>vm.runInContext(code,ctx);
  run('refresh=async()=>{};state.games=[{id:"game",version:2,name:"Hanoi",allowed_player_counts:[1]}]');
  node('new-game').value='game';node('computers').value='0';
  return {node,run};
}

test('creating a solo-only game starts one human immediately',async()=>{
  const calls=[];
  const {node,run}=setup(async(method,path,opts)=>{
    calls.push([method,path,opts.body]);
    return {id:'match',game_version:2,players:[{kind:'human',user_id:'owner'}]};
  });
  await run('createMatch()');
  assert.equal(calls.length,2);
  assert.equal(calls[0][1],'/matches');assert.equal(calls[0][2].num_computer_opponents,0);
  assert.equal(calls[1][1],'/matches/match/start');
  assert.deepEqual(Array.from(calls[1][2].first_turn_player_indices),[0]);
  assert.equal(run('state.selectedId'),'match');
  assert.equal(node('computers').disabled,true);assert.equal(node('create').textContent,'Play solo ↗');
});

test('multiplayer and mixed-mode games retain their lobby',async()=>{
  for(const counts of ['[2]','[1,2]']) {
    const calls=[];const {run}=setup(async(method,path)=>{calls.push(path);return {id:'match',game_version:2,players:[{}]};});
    run(`state.games[0].allowed_player_counts=${counts}`);await run('createMatch()');
    assert.deepEqual(calls,['/matches']);
  }
});

test('failed solo auto-start keeps the match and offers Start retry',async()=>{
  const {node,run}=setup(async(method,path)=>{
    if(path.endsWith('/start'))throw new Error('connection interrupted');
    return {id:'match',game_version:2,players:[{}]};
  });
  await run('createMatch()');assert.equal(run('state.selectedId'),'match');
  assert.match(node('create-message').textContent,/Press Start to retry/);
});

test('a valid one-seat lobby says ready instead of waiting for another player',()=>{
  const {node,run}=setup();
  run('state.gameCache.set("game@2",state.games[0]);state.match={id:"match",game_id:"game",game_version:2,owner_user_id:"owner",status:"waiting_for_players",players:[{player_index:0,kind:"human",user_id:"owner"}],move_count:0}');
  run('renderMatch()');assert.equal(node('match-status').textContent,'ready to start');
  assert.match(node('waiting').textContent,/Ready to start with 1 player/);
  assert.doesNotMatch(node('waiting').textContent,/wait for others/i);
});

test('polling preserves collapsed groups and unchanged match buttons',()=>{
  const {node,run}=setup();run('renderLists()');
  const original=node('list-over').children;
  node('group-open').open=false;node('group-over').open=false;
  run('renderLists()');
  assert.equal(node('group-open').open,false);assert.equal(node('group-over').open,false);
  assert.equal(node('list-over').children,original);
});

test('typed search finds a game in a large catalog and Enter selects it',()=>{
  const {node,run}=setup();
  run('state.games=Array.from({length:100},(_,i)=>({id:"game-"+i,name:"Puzzle "+i,version:1,allowed_player_counts:[2]}));state.gameSearchOriginal="game-0"');
  node('game-search').value='PUZZLE 83';run('onGameSearchInput()');
  assert.equal(run('state.filteredGames.length'),1);
  assert.equal(node('create').disabled,true);
  run('onGameSearchKey({key:"Enter",preventDefault(){}})');
  assert.equal(node('new-game').value,'game-83');assert.equal(node('game-search').value,'Puzzle 83');
  assert.equal(node('create').disabled,false);assert.equal(node('game-search-popup').hidden,true);
});

test('browsing the full catalog reaches games beyond the first twelve without a name',()=>{
  const {node,run}=setup();
  run('state.games=Array.from({length:100},(_,i)=>({id:"game-"+i,name:"Puzzle "+i,version:1,allowed_player_counts:[2]}));state.gameSearchOriginal="game-0"');
  node('game-search').value='previous search';run('browseAllGames()');
  assert.equal(node('game-results').children.length,100);
  assert.match(node('game-results-note').textContent,/100 games available/);
  assert.doesNotMatch(node('game-results-note').textContent,/Showing 12/);
  for(let i=0;i<99;i++)run('onGameSearchKey({key:"ArrowDown",preventDefault(){}})');
  run('onGameSearchKey({key:"Enter",preventDefault(){}})');
  assert.equal(node('new-game').value,'game-99');
  assert.equal(node('game-search').value,'Puzzle 99');
});

test('description searches remain complete and Browse all clears the filter',()=>{
  const {node,run}=setup();
  run('state.games=Array.from({length:41},(_,i)=>({id:"game-"+i,name:"Game "+i,description:i<20?"A card game":"A board game",version:1,allowed_player_counts:[2]}))');
  node('game-search').value='card';run('onGameSearchInput()');
  assert.equal(node('game-results').children.length,20);
  run('browseAllGames()');
  assert.equal(node('game-search').value,'');
  assert.equal(node('game-results').children.length,41);
});

test('unmatched search cannot create the previously selected game on blur',async()=>{
  let calls=0;const {node,run}=setup(async()=>{calls++;});
  run('state.gameSearchOriginal="game"');
  node('game-search').value='does not exist';run('onGameSearchInput();closeGameSearch()');
  assert.equal(node('new-game').value,'');assert.equal(node('create').disabled,true);
  await run('createMatch()');assert.equal(calls,0);
  assert.match(node('game-results-note').textContent,/No matching games/);
});

test('Your turn is absent when no human turn needs attention',()=>{
  const {node,run}=setup();run('renderLists()');assert.equal(node('group-turn').hidden,true);
  run('state.myMatches=[{id:"m",game_id:"game",game_version:2,players:[{player_index:0,kind:"human",user_id:"owner"}],status:"ongoing",turn_of_player_indices:[0]}];renderLists()');
  assert.equal(node('group-turn').hidden,false);assert.equal(node('count-turn').textContent,'1');
});

test('catalog updates preserve a searched choice instead of restoring an old URL choice',()=>{
  const {node,run}=setup();
  run('location.hash="#game=game";state.games.push({id:"other",version:1,name:"Other",allowed_player_counts:[2]});renderLists();chooseGame("other");state.games[0].version++;renderLists()');
  assert.equal(node('new-game').value,'other');assert.equal(node('game-search').value,'Other');
});
