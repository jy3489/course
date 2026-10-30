const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function setup(request=async()=>{}, hash='', search='') {
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id)) nodes.set(id,{value:'',hidden:false,disabled:false,dataset:{},children:[],classList:{toggle(){}},
      replaceChildren(...children){this.children=children;},setAttribute(){},addEventListener(){},focus(){}});
    return nodes.get(id);
  };
  const navigations=[];
  const pageErrors=[];
  const ctx=vm.createContext({console,Map,Set,URLSearchParams,
    navigator:{onLine:true},localStorage:{},PortalOffline:class {api(...args){return request(...args);}read(){return {};}write(){}} ,
    document:{getElementById:node,body:{classList:{toggle(){}}}},window:{addEventListener(){}},
    location:{hash,search,assign(url){navigations.push(url);}},history:{replaceState(){}},
    Auth:{current:()=>({id:'owner'})},I18n:{t:(key,params,fallback)=>fallback??key,locale:()=>'en',revision:()=>0,gameName:(id,fallback)=>fallback,gameDescription:(id,fallback)=>fallback,gameText:async()=>({locale:'en',direction:'ltr'})},GameFrame:class{load(){}unload(){}send(){}},
    apiRequest:request,formatTime:()=>'',shortId:x=>x,showPageError:text=>pageErrors.push(text),el:(tag,attrs={},...children)=>({...attrs,children})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../static/portal.js'),'utf8').replace(/main\(\);\s*$/,''),ctx);
  const run=code=>vm.runInContext(code,ctx);
  run('refresh=async()=>{};state.games=[{id:"game",version:2,name:"Hanoi",allowed_player_counts:[1]}]');
  node('new-game').value='game';
  return {node,run,navigations,pageErrors};
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
  assert.equal(node('create').textContent,'Play solo ↗');
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

test('typed search navigates to a game from a large catalog with Enter',()=>{
  const {node,run,navigations}=setup();
  run('state.games=Array.from({length:100},(_,i)=>({id:"game-"+i,name:"Puzzle "+i,version:1,allowed_player_counts:[2]}));state.gameSearchOriginal="game-0"');
  node('game-search').value='PUZZLE 83';run('onGameSearchInput()');
  assert.equal(run('state.filteredGames.length'),1);
  assert.equal(node('create').disabled,true);
  run('onGameSearchKey({key:"Enter",preventDefault(){}})');
  assert.equal(navigations[0],'/static/portal.html?game=game-83');
  assert.equal(node('new-game').value,'');assert.equal(node('game-search').value,'PUZZLE 83');
  assert.equal(node('game-search-popup').hidden,false);
});

test('browsing the full catalog reaches games beyond the first twelve without a name',()=>{
  const {node,run,navigations}=setup();
  run('state.games=Array.from({length:100},(_,i)=>({id:"game-"+i,name:"Puzzle "+i,version:1,allowed_player_counts:[2]}));state.gameSearchOriginal="game-0"');
  node('game-search').value='previous search';run('browseAllGames()');
  assert.equal(node('game-results').children.length,101);
  assert.match(node('game-results-note').textContent,/101 games available/);
  assert.doesNotMatch(node('game-results-note').textContent,/Showing 12/);
  for(let i=0;i<100;i++)run('onGameSearchKey({key:"ArrowDown",preventDefault(){}})');
  run('onGameSearchKey({key:"Enter",preventDefault(){}})');
  assert.equal(navigations[0],'/static/portal.html?game=game-99');
});

test('description searches remain complete and Browse all clears the filter',()=>{
  const {node,run}=setup();
  run('state.games=Array.from({length:41},(_,i)=>({id:"game-"+i,name:"Game "+i,description:i<20?"A card game":"A board game",version:1,allowed_player_counts:[2]}))');
  node('game-search').value='card';run('onGameSearchInput()');
  assert.equal(node('game-results').children.length,20);
  run('browseAllGames()');
  assert.equal(node('game-search').value,'');
  assert.equal(node('game-results').children.length,42);
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

test('catalog updates preserve the detail game when its version changes',()=>{
  const {node,run}=setup(async()=>{},'#game=game');
  run('state.games.push({id:"other",version:1,name:"Other",allowed_player_counts:[2]});renderLists();state.games[0].version++;renderLists()');
  assert.equal(node('new-game').value,'game');assert.equal(node('selected-game-name').textContent,'Hanoi');
});

test('gallery filters by description and player support without losing the selected match',()=>{
  const {node,run}=setup();
  run('state.games=[{id:"solo",name:"Puzzle",description:"Quiet cards",version:1,allowed_player_counts:[1]},{id:"mixed",name:"Chess",description:"Classic strategy",version:1,allowed_player_counts:[1,2]},{id:"party",name:"Party",description:"Cards with friends",version:1,allowed_player_counts:[3,4]}];state.libraryFilter="multiplayer";state.gameSearchQuery="cards";renderLibrary()');
  assert.equal(node('game-library').children.length,1);
  assert.equal(run('libraryGames()[0].id'),'party');
  run('state.libraryFilter="solo";state.gameSearchQuery="";renderLibrary()');
  assert.equal(node('game-library').children.length,2);
  run('state.gameSearchQuery="not found";renderLibrary()');
  assert.equal(node('library-empty').hidden,false);
});

test('gallery cards navigate to detail pages and polling preserves card nodes',()=>{
  const {node,run,navigations}=setup();
  run('renderLibrary()');const original=node('game-library').children;
  run('renderLibrary()');assert.equal(node('game-library').children,original);
  assert.equal(original[0].children[1].children[1].textContent,'Random');
  assert.equal(original[0].children[1].children[2].textContent,'Join an open match of any game');
  original[1].onclick();
  assert.equal(navigations[0],'/static/portal.html?game=game');
  run('state.libraryView="list";renderLibrary()');
  assert.match(node('game-library').className,/is-list/);
});

test('random game joins a randomly selected open multiplayer match',async()=>{
  const calls=[];
  const match={id:'open-1',game_id:'party',game_version:1,owner_user_id:'other',
    status:'waiting_for_players',players:[{player_index:0,kind:'human',user_id:'other'}],move_count:0};
  const {run}=setup(async(method,path,options)=>{
    calls.push([method,path,options?.body]);
    if(path==='/matches/open')return [match];
    if(path==='/games')return [{id:'party',version:1,name:'Party',allowed_player_counts:[2],published:true}];
    if(path==='/games/party/versions/1')return {id:'party',version:1,name:'Party',allowed_player_counts:[2],published:true};
    if(path==='/matches/open-1/join')return {...match,players:[...match.players,{player_index:1,kind:'human',user_id:'owner'}]};
    throw new Error(`Unexpected request ${method} ${path}`);
  });
  await run('joinRandomMatch()');
  assert.equal(run('state.selectedId'),'open-1');
  assert.ok(calls.some(([,path])=>path==='/matches/open-1/join'));
  assert.equal(calls.some(([,path])=>path==='/matches'),false);
});

test('random game creates a multiplayer lobby when no open matches exist',async()=>{
  const calls=[];
  const {run}=setup(async(method,path,options)=>{
    calls.push([method,path,options?.body]);
    if(path==='/matches/open')return [];
    if(path==='/games')return [
      {id:'solo',version:1,name:'Solo',allowed_player_counts:[1],published:true},
      {id:'party',version:1,name:'Party',allowed_player_counts:[2,4],published:true},
    ];
    if(path==='/matches')return {id:'new-lobby'};
    throw new Error(`Unexpected request ${method} ${path}`);
  });
  run('Math.random=()=>0');
  await run('joinRandomMatch()');
  const body=calls.find(([,path])=>path==='/matches')[2];
  assert.equal(body.game_id,'party');
  assert.equal(body.num_computer_opponents,0);
  assert.equal(run('state.selectedId'),'new-lobby');
});

test('random game reports a clear error when no multiplayer game can host a lobby',async()=>{
  const {run,pageErrors}=setup(async(method,path)=>{
    if(path==='/matches/open')return [];
    if(path==='/games')return [{id:'solo',version:1,name:'Solo',allowed_player_counts:[1],published:true}];
    throw new Error(`Unexpected request ${method} ${path}`);
  });
  await run('joinRandomMatch()');
  assert.match(pageErrors[0],/No published multiplayer games/);
});

test('game detail route shows the existing match controls and hides the catalog',()=>{
  const {node,run}=setup(async()=>{},'#game=game');
  run('renderLists()');
  assert.equal(node('game-catalog').hidden,true);
  assert.equal(node('game-detail-page').hidden,false);
  assert.equal(node('new-game').value,'game');
  assert.equal(node('selected-game-name').textContent,'Hanoi');
  assert.equal(node('create').textContent,'Play solo ↗');
  assert.equal(node('match-creator').open,false);
  assert.equal(node('game-catalog').hidden,true);
  assert.equal(node('game-detail-page').hidden,false);
  node('match-creator').open=true;
  run('updateMatchSetup()');
  assert.equal(node('game-catalog').hidden,false);
  assert.equal(node('game-detail-page').hidden,true);
  node('match-creator').open=false;
  run('updateMatchSetup()');
  assert.equal(node('game-catalog').hidden,true);
  assert.equal(node('game-detail-page').hidden,false);
  assert.equal(node('match-creator').open,false);
});

test('game detail route accepts query-string navigation for same-tab card clicks',()=>{
  const {node,run}=setup(async()=>{},'','?game=game');
  run('renderLists()');
  assert.equal(node('game-catalog').hidden,true);
  assert.equal(node('game-detail-page').hidden,false);
  assert.equal(node('selected-game-name').textContent,'Hanoi');
});

test('game detail route does not restore the previously selected match',()=>{
  const {run}=setup(async()=>{},'','?game=game');
  assert.equal(run('shouldRestoreSavedMatch()'),false);
});

test('selected match hides game setup while the catalog remains available',()=>{
  const {node,run}=setup(async()=>{},'#game=game');
  run('renderLists()');
  node('match-creator').open=true;
  run('updateMatchSetup()');
  run('selectMatch("match-1")');
  assert.equal(node('match-creator').open,true);
  run('updateMatchSetup()');
  assert.equal(node('game-detail-page').hidden,true);
  assert.equal(node('game-catalog').hidden,false);
  node('match-creator').open=false;
  run('updateMatchSetup()');
  assert.equal(node('game-detail-page').hidden,true);
  assert.equal(node('game-catalog').hidden,true);
});

test('finished multiplayer match offers a rematch to each human player',async()=>{
  const calls=[];
  const {node,run}=setup(async(method,path,options)=>{
    calls.push([method,path,options?.user]);
    return {id:'rematch-id',game_id:'game',game_version:2,status:'waiting_for_players'};
  });
  run('state.gameCache.set("game@2",state.games[0]);state.match={id:"finished-id",game_id:"game",game_version:2,owner_user_id:"owner",status:"over",players:[{player_index:0,kind:"human",user_id:"owner"},{player_index:1,kind:"human",user_id:"other"}],move_count:3,state:{winner:0}};renderMatch()');
  const requestButton=node('actions').children.find(button=>button.textContent==='Request rematch');
  assert.ok(requestButton);
  await requestButton.onclick();
  assert.deepEqual(calls[0].slice(0,2),['POST','/matches/finished-id/rematch']);
  assert.equal(run('state.selectedId'),'rematch-id');
});

test('finished solo-versus-computers match offers Play again',()=>{
  const {node,run}=setup();
  run('state.gameCache.set("game@2",state.games[0]);state.match={id:"finished-id",game_id:"game",game_version:2,owner_user_id:"owner",status:"over",players:[{player_index:0,kind:"human",user_id:"owner"},{player_index:1,kind:"computer",user_id:null}],move_count:3,state:{winner:0}};renderMatch()');
  assert.ok(node('actions').children.some(button=>button.textContent==='Play again'));
  assert.ok(!node('actions').children.some(button=>button.textContent==='Request rematch'));
});

test('finished results are personalized including seat zero, draws, and spectators',()=>{
  const {run}=setup();
  run('state.match={status:"over",end_reason:"finished",players:[{player_index:0,user_id:"owner",kind:"human"},{player_index:1,user_id:"other",kind:"human"}],state:{winner:0}}');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'You won');
  assert.equal(run('matchOutcome(state.match,"other").label'),'You lost');
  assert.doesNotMatch(run('matchOutcome(state.match,"spectator").label'),/You/);
  run('state.match.state={winner:null,draw:true}');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'Draw');
  run('state.match.state={winner:null,done:true}');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'Draw');
});

test('unfinished, abandoned, unknown and unsynced results are not false confirmed losses',()=>{
  const {run}=setup();
  run('state.match={status:"ongoing",players:[],state:{winner:0}}');
  assert.equal(run('matchOutcome(state.match,"owner")'),null);
  run('state.match.status="over";state.match.end_reason="player_left"');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'Ended early');
  run('state.match.end_reason="finished";state.match.state={winner:null}');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'Result unavailable');
  run('state.match.state={winner:null,done:true};state.match.pending=1');
  assert.match(run('matchOutcome(state.match,"owner").label'),/awaiting sync/);
});

test('completed games using winner minus one show Draw for players and spectators',()=>{
  const {run,node}=setup();
  run('state.gameCache.set("game@2",state.games[0]);state.match={id:"nine-stars-draw",game_id:"game",game_version:2,owner_user_id:"owner",status:"over",end_reason:"finished",players:[{player_index:0,kind:"human",user_id:"owner"},{player_index:1,kind:"computer",user_id:null}],move_count:10,state:{winner:-1}};renderMatch()');
  assert.equal(node('match-result').textContent,'Draw');
  assert.equal(run('matchOutcome(state.match,"owner").kind'),'draw');
  assert.equal(run('matchOutcome(state.match,"spectator").label'),'Draw');
  run('state.match.pending=1');assert.equal(run('matchOutcome(state.match,"owner").label'),'Draw · awaiting sync');
  run('state.match.pending=0;state.match.end_reason="player_left"');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'Ended early');
  run('state.match.status="ongoing"');assert.equal(run('matchOutcome(state.match,"owner")'),null);
  run('state.match.status="over";state.match.end_reason="finished";state.match.state.winner=-2');
  assert.equal(run('matchOutcome(state.match,"owner").label'),'Result unavailable');
});


test('match history navigates read-only snapshots and returns to live',async()=>{
  const moves=[{new_state:{score:1},next_turn_player_indices:[0]},{new_state:{score:2},next_turn_player_indices:null}];
  const t=setup(async()=>({moves,initial_state:null,initial_turn_player_indices:null}));
  t.run('state.match={id:"match",move_count:2};syncGame=async()=>{}');
  await t.run('browseHistory(-1)');assert.equal(t.run('state.replay.index'),1);
  assert.equal(t.run('state.replay.move.new_state.score'),1);
  assert.equal(t.node('history-back').disabled,true);
  await t.run('onMakeMove({new_state:99,next_turn_player_indices:null})');
  assert.equal(t.run('state.match.move_count'),2);
  await t.run('browseHistory(1)');assert.equal(t.run('state.replay.index'),2);
  assert.equal(t.node('history-forward').disabled,true);
  await t.run('browseHistory("live")');assert.equal(t.run('state.replay'),null);
});
test('history responses for a previously selected match are discarded',async()=>{
  let resolve;const response=new Promise(r=>resolve=r),t=setup(()=>response);
  t.run('state.match={id:"old",move_count:2}');const pending=t.run('browseHistory(-1)');
  t.run('state.match={id:"new",move_count:3}');resolve({moves:[{new_state:1}],initial_state:null,initial_turn_player_indices:null});await pending;
  assert.equal(t.run('state.replay'),undefined);
});


test('history reaches a recorded custom initial state at move zero',async()=>{
  const t=setup(async()=>({initial_state:{score:42},initial_turn_player_indices:[1],moves:[{new_state:{score:43},next_turn_player_indices:[0]}]}));
  t.run('state.match={id:"match",move_count:1};syncGame=async()=>{}');
  await t.run('browseHistory(-1)');assert.equal(t.run('state.replay.index'),0);
  assert.equal(t.run('state.replay.move.new_state.score'),42);assert.equal(t.node('history-back').disabled,true);
});

test('invalid next seats are rejected before a move is durably queued',async()=>{
  const t=setup();t.run('navigator.locks={request:async(name,run)=>run()};state.match={id:"m",game_id:"g",game_version:1,status:"ongoing",turn_of_player_indices:[0],players:[{player_index:0,user_id:"owner",kind:"human"}]};state.loaded="m@g@1";offline.enqueue=()=>{throw new Error("must not enqueue")};offline.sync=async()=>{}');
  await t.run('onMakeMove({new_state:{score:1},next_turn_player_indices:[1]})');
  assert.match(t.node('match-message').textContent,/invalid next player/);
});
test('a second v2 move is not queued behind a pending one',async()=>{
  const t=setup();
  t.run('navigator.locks={request:async(name,run)=>run()};state.match={id:"m",game_id:"g",game_version:1,status:"ongoing",turn_of_player_indices:[0],move_count:3,players:[{player_index:0,user_id:"owner",kind:"human"}]};state.loaded="m@g@1";state.enq=0;state.forces=[];offline.enqueue=(match,body)=>{state.enq++;return match};offline.sync=async(opts)=>{state.forces.push(opts&&opts.force)};offline.read=()=>({queues:{}})');
  await t.run('onMakeMove({move:{type:"call"}})');
  assert.equal(t.run('state.enq'),1);
  assert.deepEqual([...t.run('state.forces')],[true]);
  // The first move is now stuck waiting to sync: another click is a
  // decision on the same stale view and must not queue behind it.
  t.run('offline.read=()=>({queues:{m:[{body:{move:{type:"call"},expected_move_count:3}}]}})');
  await t.run('onMakeMove({move:{type:"fold"}})');
  assert.equal(t.run('state.enq'),1);
  assert.deepEqual([...t.run('state.forces')],[true,true]);
});
test('switching matches unloads the old iframe immediately',()=>{
  const t=setup();t.run('state.loaded="old@g@1";frame.unload=()=>{state.unloaded=true};selectMatch("new")');
  assert.equal(t.run('state.unloaded'),true);assert.equal(t.run('state.loaded'),null);
});
test('late game-code responses cannot populate a newly selected match',async()=>{
  let resolve;const wait=new Promise(r=>resolve=r);const t=setup(()=>wait);
  t.run('state.match={id:"old",game_id:"g",game_version:1,status:"ongoing"};state.loaded=null;frame.load=()=>{state.badLoad=true}');
  const pending=t.run('syncGame()');t.run('state.match={id:"new"}');resolve({code:'old game'});await pending;
  assert.equal(t.run('state.badLoad'),undefined);assert.equal(t.run('state.loaded'),null);
});

test('current account avatar wins over stale match profile cache',()=>{
  const {run}=setup();
  run('Auth.current=()=>({id:"owner",picture_url:"/saved-deer.png"});state.pictureCache.set("owner","/old-puppy.png")');
  assert.equal(run('playerPicture({kind:"human",user_id:"owner"})'),'/saved-deer.png');
  run('state.pictureCache.set("other","/other-cat.png")');
  assert.equal(run('playerPicture({kind:"human",user_id:"other"})'),'/other-cat.png');
});
