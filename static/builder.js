// A playable workspace with automatic working-copy saves and public version releases.
"use strict";
const $ = id => document.getElementById(id);
const EMPTY = {name:"",description:"",code:"",allowed_player_counts:[2],allows_leave_mid_match:false,allows_join_mid_match:false};
const content = value => Object.fromEntries(Object.keys(EMPTY).map(key => [key, value[key] ?? EMPTY[key]]));
const copy = value => JSON.parse(JSON.stringify(value));
const state = {games:[],drafts:[],selectedId:null,editor:copy(EMPTY),baseline:null,session:0,
  savePromise:null,saveTimer:null,saveError:"",publishing:false,restoring:false,deleteArmed:false,
  agentRequest:null,agentSending:false,agentDebug:false,messages:[],log:[],undo:[],test:null,
  aiOpen:true,panelPosition:null,saveWarning:"",ownerId:null};
const frame = new GameFrame($("test-frame"), onTestMove, problem => testLog(problem,true));
const me = () => Auth.current();
const selectedGame = () => state.games.find(game => game.id === state.selectedId);
const selectedDraft = () => state.drafts.find(game => game.id === state.selectedId);
const editorValues = () => copy(state.editor);
const isDirty = () => state.baseline !== null && JSON.stringify(state.editor) !== state.baseline;
const currentSession = (session,user) => state.session === session && me()?.id === user.id;
const recoveryKey = (id=state.selectedId,user=me()) => "game-platform.builder.work.v2." + user.id + "." + id;
function recoveryRecord() {
  return {values:state.editor,baseline:state.baseline,revision:selectedDraft()?.revision ?? null,messages:state.messages,log:state.log};
}
function rememberWork() {
  if (!me() || !state.selectedId || state.ownerId !== me().id) return;
  try {
    localStorage.setItem(recoveryKey(),JSON.stringify(recoveryRecord()));
    localStorage.setItem("game-platform.builder.last.v2."+me().id,state.selectedId);
    state.saveWarning="";
  }
  catch { state.saveWarning="Device recovery is unavailable. Keep this page open until autosave succeeds."; }
}
function restoreWork() {
  try {
    const record=JSON.parse(localStorage.getItem(recoveryKey()) || "null");
    if (!record) return;
    state.messages=record.messages || []; state.log=record.log || [];
    if (JSON.stringify(record.values) !== record.baseline) {
      if (record.revision !== (selectedDraft()?.revision ?? null)) {
        localStorage.setItem(recoveryKey()+".conflict."+Date.now(),JSON.stringify(record));
        state.saveWarning="A newer copy was saved elsewhere. Your recovered edits are kept on this device; reload before editing.";
      } else { state.editor=content(record.values); state.baseline=record.baseline; }
    }
  } catch { state.saveWarning="Couldn't restore this device's working copy."; }
}
async function loadGames() {
  const user=me();
  const [games,drafts]=user ? await Promise.all([apiRequest("GET","/builder/games",{user}),apiRequest("GET","/game-drafts",{user})]) : [[],[]];
  if (me()?.id !== user?.id) return;
  state.games=games; state.drafts=drafts;
}
function clearAgent() {
  if (state.agentRequest) {state.agentRequest.cancelled=true;state.agentRequest.controller.abort();}
  state.agentRequest=null; state.agentSending=false;
  $("agent-activity").hidden=true; $("agent-stop").hidden=true;
}
async function select(id) {
  if (!me() || state.selectedId === id || state.publishing || state.restoring) return;
  if (isDirty() && !(await flushAutosave())) return;
  clearTimeout(state.saveTimer); clearAgent(); state.session++;
  state.selectedId=id; state.ownerId=me()?.id; state.undo=[]; state.deleteArmed=false; state.saveError="";state.saveWarning="";
  state.messages=[];state.log=[]; state.aiOpen=true;
  state.editor=content(selectedDraft() ?? selectedGame() ?? EMPTY);
  state.baseline=JSON.stringify(state.editor);
  restoreWork(); stopTest(); $("agent-input").value=""; render();
  try {localStorage.setItem("game-platform.builder.last.v2."+me().id,id);} catch {}
  if (state.editor.code) {renderPlayModes();startTest();}
  if (isDirty()) scheduleAutosave();
}
function scheduleAutosave() {
  state.deleteArmed=false;
  rememberWork(); clearTimeout(state.saveTimer);
  state.saveTimer=setTimeout(() => flushAutosave(),600);
  renderStatus();
}
async function flushAutosave() {
  clearTimeout(state.saveTimer);
  if (state.savePromise) {
    const succeeded=await state.savePromise;
    return succeeded && isDirty() ? flushAutosave() : succeeded;
  }
  if (!isDirty()) return true;
  const user=me(), id=state.selectedId, session=state.session;
  if (!user || !id) return false;
  const values=editorValues(), baseline=JSON.stringify(values),revision=selectedDraft()?.revision ?? null;
  state.saveError="";
  const operation=(async()=>{
    try {
      const draft=await apiRequest(id === "new" ? "POST":"PUT",id === "new" ? "/game-drafts":"/game-drafts/"+encodeURIComponent(id),
        {user,body:{...values,expected_revision:revision}});
      if (!currentSession(session,user)) return false;
      state.drafts=[draft,...state.drafts.filter(item=>item.id!==draft.id)];
      state.selectedId=draft.id; state.baseline=baseline;
      if (id !== draft.id) {try {localStorage.removeItem(recoveryKey(id,user));} catch {}}
      rememberWork(); renderLibrary(); return true;
    } catch(error) {
      if (currentSession(session,user)) {state.saveError="Autosave failed: "+error.message;rememberWork();}
      return false;
    }
  })();
  state.savePromise=operation;renderStatus();
  const succeeded=await operation;
  if (state.savePromise===operation) state.savePromise=null;
  if (!currentSession(session,user)) return false;
  renderStatus();
  return succeeded && isDirty() ? flushAutosave() : succeeded;
}
async function publishVersion() {
  if (state.publishing || state.agentSending || state.restoring || !state.editor.code) return;
  state.publishing=true;state.saveError="";renderStatus();
  const user=me(), session=state.session;
  try {
    if (!(await flushAutosave())) return;
    const draft=selectedDraft();
    if (!draft) return;
    const game=await apiRequest("POST","/game-drafts/"+encodeURIComponent(draft.id)+"/publish",{user,body:{expected_revision:draft.revision}});
    if (!currentSession(session,user)) return;
    state.games=[game,...state.games.filter(item=>item.id!==game.id)];
    state.drafts=state.drafts.map(item=>item.id===game.id ? {...item,base_version:game.version}:item);
    agentLog("system","Published version "+game.version+". Everyone can play this version.");
    rememberWork();render();
  } catch(error) {if (currentSession(session,user)) state.saveError="Couldn't publish: "+error.message;}
  finally {if(currentSession(session,user)){state.publishing=false;renderStatus();}}
}
function changeName() {
  state.editor.name=$("game-name").value;state.deleteArmed=false;scheduleAutosave();
}
function renderLibrary() {
  const library=new Map(state.games.map(game=>[game.id,game]));
  for (const draft of state.drafts) library.set(draft.id,draft);
  const items=[...library.values()].map(item=>el("button",{type:"button",class:"list-item","aria-selected":String(item.id===state.selectedId),onclick:()=>select(item.id)},
    el("span",{class:"title",textContent:item.name || "Untitled game"}),
    el("span",{class:"sub",textContent:state.games.find(game=>game.id===item.id)?.version ? "v"+state.games.find(game=>game.id===item.id).version:"In progress"})));
  if (state.selectedId==="new") items.push(el("button",{type:"button",class:"list-item","aria-selected":"true",textContent:"New game"}));
  if (!items.length) items.push(el("p",{class:"muted",textContent:me()?"Your games will appear here.":"Sign in to build a game."}));
  $("games").replaceChildren(...items);
}
function renderStatus() {
  const game=selectedGame();
  const changed=!game || game.published===false || JSON.stringify(content(game))!==JSON.stringify(state.editor);
  $("publish").disabled=!state.editor.code || !state.editor.name.trim() || !changed || state.publishing || state.agentSending || state.restoring;
  $("publish").textContent=state.publishing ? "Publishing…":"Publish";
  $("save-status").textContent=state.saveError || state.saveWarning || (state.savePromise ? "Saving…":isDirty()?"Saving…":state.selectedId && state.editor.code?"Autosaved":"");
  $("save-status").className="save-status"+((state.saveError||state.saveWarning)?" error":"");
  $("retry-save").hidden=!state.saveError;
  $("undo-ai-change").disabled=!state.undo.length || state.agentSending || state.publishing || state.restoring;
  $("new-game").disabled=!me() || state.publishing;
  $("game-name").disabled=state.publishing || state.restoring;
  $("load-version").disabled=state.publishing || state.restoring || state.agentSending;
  $("delete-game").disabled=state.publishing || state.restoring || state.agentSending || Boolean(state.savePromise);
  $("agent-send").disabled=state.agentSending || state.publishing || state.restoring;
  $("agent-send").textContent=state.agentSending?"Working…":state.editor.code?"Update game":"Create game";
  $("open-ai").hidden=!state.editor.code || state.aiOpen;
  $("open-ai").textContent=state.agentSending?"AI is working…":"Ask AI";
  $("agent-stop").hidden=!state.agentSending;
  $("game-summary").textContent=state.editor.description;
  $("game-version").textContent=game ? "v"+game.version:"First version";
  $("play-game").hidden=!game || game.published===false;
  if (game) $("play-game").href="/portal#game="+encodeURIComponent(game.id);
}
function render() {
  renderLibrary();
  const ready=Boolean(me() && state.selectedId);
  $("workspace").hidden=!ready;$("welcome").hidden=ready;
  $("welcome-action").textContent=me()?"Create a game":"Sign in";
  const playable=Boolean(state.editor.code);
  $("starter-view").hidden=playable;$("play-view").hidden=!playable;
  $("ai-panel").hidden=!ready || !playable || !state.aiOpen;
  const host=$(playable?"ai-panel-body":"starter-agent-home");
  if ($("agent-tools").parentNode!==host) host.append($("agent-tools"));
  $("game-name").value=state.editor.name;
  $("agent-input").placeholder=playable?"Change a rule, add a feature, or fix a bug…":"Describe the game you want to play…";
  const log=$("agent-log");
  log.hidden=!state.log.length;
  log.replaceChildren(...state.log.map(item=>el("div",{class:"msg "+item.role,textContent:item.text})));
  log.scrollTop=log.scrollHeight;
  const game=selectedGame();
  $("versions").replaceChildren(...(game?Array.from({length:game.version},(_,i)=>el("option",{value:game.version-i,textContent:"Version "+(game.version-i)})):[]));
  $("version-history").hidden=!game;
  renderStatus();
}
function agentLog(role,text) {
  state.log.push({role,text});state.log=state.log.slice(-80);
  $("agent-log").hidden=false;
  $("agent-log").append(el("div",{class:"msg "+role,textContent:text}));
  $("agent-log").scrollTop=$("agent-log").scrollHeight;
}
function testLog(text,isError=false) {
  if (isError) $("test-status").textContent=text;
}
function renderPlayModes() {
  const choices=state.editor.allowed_player_counts.flatMap(count=>count===1
    ? [{value:"1:solo",label:"Solo"}]
    : [{value:count+":computer",label:count+" players · vs computers"},{value:count+":human",label:count+" players · pass and play"}]);
  const previous=$("play-mode").value;
  $("play-mode").replaceChildren(...choices.map(choice=>el("option",{value:choice.value,textContent:choice.label})));
  $("play-mode").value=choices.some(choice=>choice.value===previous)?previous:choices[0]?.value || "";
}
function startTest() {
  if (!state.editor.code) return;
  const [countText,mode]=$("play-mode").value.split(":");const count=Number(countText);
  if (!state.editor.allowed_player_counts.includes(count)) return;
  const players=Array.from({length:count},(_,seat)=>({player_index:seat,kind:mode==="computer"&&seat>0?"computer":"human",name:mode==="computer"&&seat>0?"Computer "+seat:"Player "+(seat+1)}));
  state.test={code:state.editor.code,players,moves:[],state:null,turn:[0],status:"ongoing",awaiting:false};
  frame.load(state.editor.code);sendTestState();
}
function stopTest(){state.test=null;frame.unload();$("test-status").textContent="";}
function setMessage(id,text,kind="") {$(id).textContent=text;$(id).className="message "+kind;}
function sendTestState() {
  const test = state.test;
  const firstTurn = test.turn === null ? null : test.turn[0];
  const turnSeat = firstTurn === null ? null : test.players[firstTurn];
  const message = stateChangedMessage({
    state: test.state,
    players: test.players,
    turn: test.turn,
    status: test.status,
    endReason: test.status === "over" ? "finished" : null,
    moveCount: test.moves.length,
    // In pass-and-play the person at the screen is whichever human has the turn.
    mySeat: turnSeat && turnSeat.kind === "human" ? firstTurn : null,
    actingFor: test.status === "ongoing" ? firstTurn : null,
  });
  test.awaiting = test.status === "ongoing";
  frame.send(message);
  setMessage("test-status",
    test.status === "over"
      ? `The game ended after ${test.moves.length} moves.`
      : `Move ${test.moves.length + 1}: ${test.turn.map((seat) => test.players[seat].name).join(", ")}'s turn.`,
    test.status === "over" ? "ok" : "");
  testLog(`sent state_changed: move_count=${test.moves.length} turn=${JSON.stringify(test.turn)} status=${test.status}`);
}

function onTestMove(move) {
  const test = state.test;
  if (!test || !test.awaiting) {
    testLog("ignored make_move: no move is expected now (the game already moved, or it's over)", true);
    return;
  }
  const next = move.next_turn_player_indices;
  const valid = next === null ||
    (Array.isArray(next) && next.length > 0 && new Set(next).size === next.length &&
      next.every((seat) => Number.isInteger(seat) && seat >= 0 && seat < test.players.length));
  if (!valid) {
    testLog(`rejected make_move: next_turn_player_indices ${JSON.stringify(next)} is not a set of seats`, true);
    sendTestState();
    return;
  }
  test.awaiting = false;
  test.moves.push({ seats: test.turn, new_state: move.new_state, next });
  testLog(`received make_move from seats ${JSON.stringify(test.turn)}: next=${JSON.stringify(next)} new_state=${JSON.stringify(move.new_state)}`);
  test.state = move.new_state;
  test.turn = next;
  test.status = next === null ? "over" : "ongoing";
  sendTestState();
}

function undoTestMove() {
  const test = state.test;
  if (!test || test.moves.length === 0) return;
  test.moves.pop();
  const last = test.moves[test.moves.length - 1];
  test.state = last ? last.new_state : null;
  test.turn = last ? last.next : [0];
  test.status = "ongoing";
  testLog("undid the last move");
  sendTestState();
}


function generationActivity(phase, started) {
  $("agent-activity").hidden = false;
  if ($("agent-phase").textContent !== phase) $("agent-phase").textContent = phase;
  const seconds = Math.floor((Date.now() - started) / 1000);
  $("agent-elapsed").textContent = `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed`;
  $("agent-wait-note").textContent = seconds >= 60
    ? "Still waiting for AI. You can stop waiting and keep your current game."
    : "Your current game stays available while AI works.";
}

function stopAgentWaiting() {
  const request = state.agentRequest;
  if (!request) return;
  request.cancelled = true;
  request.controller.abort();
  setMessage("agent-message", "Stopping this browser's wait…");
}

async function requestAgentReply(user, body, isCurrent, request) {
  const started = Date.now();
  const options = {user, signal: request.controller.signal};
  let phase = "Asking AI…";
  generationActivity(phase, started);
  const ticker = setInterval(() => {
    if (isCurrent() && !request.cancelled) generationActivity(phase, started);
  }, 1000);
  try {
    let reply = await apiRequest("POST", "/agent/chat", {...options, body});
    const deadline = started + 16 * 60 * 1000;
    while (reply?.preview_job_id) {
      if (!isCurrent() || request.cancelled) throw new Error("Waiting stopped.");
      if (Date.now() > deadline) throw new Error("Generation is taking too long. Please try again later.");
      phase = "AI is working…";
      generationActivity(phase, started);
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (request.cancelled) throw new Error("Waiting stopped.");
      reply = await apiRequest("GET", `/preview/jobs/${encodeURIComponent(reply.preview_job_id)}`, options);
    }
    if (request.cancelled) throw new Error("Waiting stopped.");
    return reply;
  } finally {
    clearInterval(ticker);
  }
}


async function sendAgentMessage() {
  const user=me(),input=$("agent-input").value.trim();
  if (!user||!input||state.agentSending||state.publishing||state.restoring) return;
  const session=state.session,values=editorValues(),base=JSON.stringify(values);
  const request={cancelled:false,controller:new AbortController()};state.agentRequest=request;state.agentSending=true;
  const isCurrent=()=>currentSession(session,user)&&state.agentRequest===request;
  const messages=[...state.messages.slice(-48),{role:"user",content:input}];
  $("agent-input").value="";agentLog("user",input);setMessage("agent-message","");rememberWork();renderStatus();
  try {
    const reply=await requestAgentReply(user,{game_name:values.name,game_description:values.description,
      allowed_player_counts:values.allowed_player_counts,player_count_mode:"auto",
      allows_leave_mid_match:values.allows_leave_mid_match,allows_join_mid_match:values.allows_join_mid_match,
      code:values.code,messages},isCurrent,request);
    if (!isCurrent()||request.cancelled) return;
    state.messages=[...messages,{role:"assistant",content:reply.message}];
    agentLog("assistant",reply.message);
    if (reply.code) {
      const counts=reply.allowed_player_counts;
      if (!Array.isArray(counts)||!counts.length||new Set(counts).size!==counts.length||counts.some(n=>!Number.isInteger(n)||n<1||n>10)) throw new Error("The AI did not return valid player counts. Please ask it to try again.");
      if ([reply.allows_leave_mid_match,reply.allows_join_mid_match].some(value=>value!=null && typeof value!=="boolean")) throw new Error("The AI did not return valid mid-match rules. Please ask it to try again.");
      if (JSON.stringify(state.editor)!==base) {
        agentLog("system","The game changed while the AI was working, so this update wasn't applied. Ask again using the latest game.");
      } else {
        state.undo.push(editorValues());state.undo=state.undo.slice(-8);
        state.editor={...state.editor,code:reply.code,allowed_player_counts:counts,
          allows_leave_mid_match:reply.allows_leave_mid_match ?? state.editor.allows_leave_mid_match,
          allows_join_mid_match:reply.allows_join_mid_match ?? state.editor.allows_join_mid_match,
          name:reply.name || state.editor.name || input.split("\n")[0].slice(0,80),description:reply.description ?? state.editor.description};
        render();renderPlayModes();startTest();scheduleAutosave();
        agentLog("system","Updated the game. Play it now, ask for another change, or publish a version when ready.");
      }
    }
    rememberWork();
  } catch(error) {
    if (!isCurrent()) return;
    setMessage("agent-message",request.cancelled?"Stopped waiting. Your game is unchanged; the server may still finish this request.":"The agent couldn't reply: "+error.message,"error");
    if (!$("agent-input").value.trim()) $("agent-input").value=input;
  } finally {
    if (isCurrent()) {state.agentSending=false;state.agentRequest=null;$("agent-activity").hidden=true;renderStatus();}
  }
}
function undoAppliedChange() {
  if (!state.undo.length||state.agentSending||state.publishing||state.restoring) return;
  state.editor=state.undo.pop();render();renderPlayModes();startTest();scheduleAutosave();
  agentLog("system","Undid the last AI change.");
}
async function restoreVersion() {
  if (state.agentSending||state.publishing||state.restoring||!selectedGame()) return;
  if (!(await flushAutosave())) return;
  const user=me(),session=state.session,id=state.selectedId,version=$("versions").value;
  state.restoring=true;renderStatus();
  try {
    const value=await apiRequest("GET","/games/"+encodeURIComponent(id)+"/versions/"+version,{user});
    if (!currentSession(session,user)) return;
    state.undo.push(editorValues());state.editor=content(value);render();renderPlayModes();startTest();scheduleAutosave();
    agentLog("system","Loaded version "+version+". Publish to release it as the next version.");
  } catch(error){if(currentSession(session,user))state.saveError="Couldn't load the version: "+error.message;}
  finally {if(currentSession(session,user)){state.restoring=false;renderStatus();}}
}
async function deleteGame() {
  if (state.publishing||state.agentSending||state.savePromise||!state.selectedId) return;
  if (!state.deleteArmed) {state.deleteArmed=true;setMessage("workspace-message","Click Delete game again to remove it. Existing matches retain their history.","error");return;}
  const user=me(),session=state.session,id=state.selectedId;
  try {
    clearTimeout(state.saveTimer);
    if (id!=="new") await apiRequest("DELETE",(selectedGame()?"/games/":"/game-drafts/")+encodeURIComponent(id),{user});
    if (!currentSession(session,user)) return;
    try{localStorage.removeItem(recoveryKey());}catch{}
    state.selectedId=null;state.baseline=null;state.editor=copy(EMPTY);state.session++;stopTest();clearAgent();
    await loadGames();render();
  } catch(error){setMessage("workspace-message","Couldn't delete: "+error.message,"error");}
}
function clampPanel(x,y,width,height,viewportWidth,viewportHeight) {
  return {x:Math.max(8,Math.min(x,viewportWidth-width-8)),y:Math.max(8,Math.min(y,viewportHeight-Math.min(height,viewportHeight-16)-8))};
}
function positionPanel(x,y) {
  const panel=$("ai-panel"),rect=panel.getBoundingClientRect();
  const position=clampPanel(x,y,rect.width,rect.height,window.innerWidth,window.innerHeight);
  state.panelPosition=position;panel.style.left=position.x+"px";panel.style.top=position.y+"px";panel.style.right="auto";panel.style.bottom="auto";
}
function initFloatingPanel() {
  const handle=$("ai-drag-handle");let drag=null;
  handle.addEventListener("pointerdown",event=>{
    if(event.button!==0)return;
    const rect=$("ai-panel").getBoundingClientRect();drag={id:event.pointerId,x:event.clientX-rect.x,y:event.clientY-rect.y};
    handle.setPointerCapture(event.pointerId);event.preventDefault();
  });
  handle.addEventListener("pointermove",event=>{if(drag?.id===event.pointerId)positionPanel(event.clientX-drag.x,event.clientY-drag.y);});
  const end=()=>{drag=null;};handle.addEventListener("pointerup",end);handle.addEventListener("pointercancel",end);
  handle.addEventListener("keydown",event=>{
    const direction={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];
    if(!direction)return;event.preventDefault();const rect=$("ai-panel").getBoundingClientRect();positionPanel(rect.x+direction[0],rect.y+direction[1]);
  });
  window.addEventListener("resize",()=>{if(state.panelPosition)positionPanel(state.panelPosition.x,state.panelPosition.y);});
  $("close-ai").addEventListener("click",()=>{state.aiOpen=false;render();$("open-ai").focus();});
  $("open-ai").addEventListener("click",()=>{state.aiOpen=true;render();if(state.panelPosition)positionPanel(state.panelPosition.x,state.panelPosition.y);$("agent-input").focus();});
}
async function onUserChanged() {
  clearTimeout(state.saveTimer);clearAgent();state.session++;
  state.selectedId=null;state.baseline=null;state.editor=copy(EMPTY);state.savePromise=null;state.saveError="";state.saveWarning="";
  state.ownerId=null;state.publishing=false;state.restoring=false;state.games=[];state.drafts=[];
  state.messages=[];state.log=[];state.undo=[];stopTest();render();
  try{await loadGames();await resumeWorkspace();}catch(error){showPageError("Couldn't load games: "+error.message);}
}
async function resumeWorkspace() {
  let last;
  try {last=me() && localStorage.getItem("game-platform.builder.last.v2."+me().id);} catch {}
  if (last && (state.games.some(game=>game.id===last) || state.drafts.some(game=>game.id===last) ||
      (last==="new" && localStorage.getItem(recoveryKey("new"))))) await select(last);
  else render();
}
async function main() {
  $("new-game").addEventListener("click",()=>select("new"));
  $("welcome-action").addEventListener("click",()=>me()?select("new"):Auth.openDialog("login"));
  $("publish").addEventListener("click",publishVersion);$("retry-save").addEventListener("click",()=>flushAutosave());
  $("game-name").addEventListener("input",changeName);$("undo-ai-change").addEventListener("click",undoAppliedChange);
  $("play-mode").addEventListener("change",startTest);$("restart").addEventListener("click",startTest);
  $("undo-move").addEventListener("click",undoTestMove);$("load-version").addEventListener("click",restoreVersion);
  $("delete-game").addEventListener("click",deleteGame);$("agent-send").addEventListener("click",sendAgentMessage);
  $("agent-stop").addEventListener("click",stopAgentWaiting);
  $("agent-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&(event.ctrlKey||event.metaKey))sendAgentMessage();});
  document.addEventListener("auth-changed",()=>{Auth.renderMenu($("account-menu"));onUserChanged();});
  window.addEventListener("beforeunload",event=>{rememberWork();if(isDirty()||state.savePromise||state.agentSending){event.preventDefault();event.returnValue="";}});
  document.addEventListener("visibilitychange",()=>{if(document.hidden&&isDirty())flushAutosave();});
  Auth.renderMenu($("account-menu"));initFloatingPanel();
  try {await loadGames();await resumeWorkspace();}catch(error){showPageError("Couldn't load games: "+error.message);}
}
main();
