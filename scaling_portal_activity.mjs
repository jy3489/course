// Real portal workload. All player mutations are browser clicks. Only private
// harness controls and validation reads use HTTP directly; no AI is called.
import fs from 'node:fs';
import path from 'node:path';
import {chromium, expect} from '@playwright/test';

const args=process.argv.slice(2);
function arg(name) {const i=args.indexOf(name); if(i<0 || !args[i+1]) throw new Error(`Missing ${name}`); return args[i+1];}
if(!args.includes('--isolated-test-only')) throw new Error('--isolated-test-only is required');
const config=JSON.parse(fs.readFileSync(arg('--config'),'utf8'));
if(!/^[a-zA-Z0-9_-]{1,48}$/.test(config.runId??'')) throw new Error('A short alphanumeric runId is required');
const reportPath=path.resolve(arg('--report'));
const base=new URL(config.baseUrl);
if(base.hostname!=='127.0.0.1' || base.protocol!=='http:') throw new Error('Use a loopback HTTP URL or authenticated SSH tunnel');
const days=config.days??30, perDay=config.matchesPerDay??1, maxActions=config.maxActions??3000;
if(!Number.isInteger(days)||days<1||days>31||!Number.isInteger(perDay)||perDay<1||perDay>1000||!Number.isInteger(maxActions)||maxActions<1||maxActions>100000) throw new Error('Invalid workload limits');
const control=JSON.parse(fs.readFileSync(config.controlFile,'utf8'));
async function admin(method, endpoint, data) {
  const response=await fetch(new URL(endpoint,base),{method,headers:{Authorization:`Bearer ${control.token}`,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  if(!response.ok) throw new Error(`Harness ${endpoint}: ${response.status}`);
  return response.json();
}
const info=await admin('GET','/workload/config');
if(info.database==='(default)'||info.namespace!==config.namespace) throw new Error('Isolated namespace mismatch');
const games=info.catalog.filter(game=>game.kind==='tictactoe');
if(!games.length) throw new Error('No copied tic-tac-toe games');
const report={runId:config.runId,baseUrl:config.baseUrl,namespace:info.namespace,month:info.month,
  startedAt:new Date().toISOString(),kind:'portal browser activity on duplicated shipped games',
  gameSourceHashes:[...new Set(info.catalog.map(x=>x.source_sha256))],before:await admin('GET','/workload/counts'),
  actions:0,completed:[],failures:[],pageErrors:[],httpErrors:[]};
function save(){fs.mkdirSync(path.dirname(reportPath),{recursive:true});const temp=reportPath+'.tmp';fs.writeFileSync(temp,JSON.stringify(report,null,2));fs.renameSync(temp,reportPath);}
let seed=Number(config.seed??42)>>>0;
let previousGuest=null;
function random(){seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;}
async function click(locator){if(++report.actions>maxActions) throw new Error('UI action budget exhausted');await locator.click();save();}
const browser=await chromium.launch({headless:true});
save();
let exitCode=0;
try {
  for(let day=1;day<=days;day++) {
    const date=`${info.month}-${String(day).padStart(2,'0')}`;
    await admin('POST','/workload/clock',{date});
    for(let n=0;n<perDay;n++) {
      const index=(day-1)*perDay+n, contexts=[], pages=[], users=[];
      const twoHumans=random()<(config.humanOpponentProbability??0.5);
      const leave=random()<(config.leaveProbability??0.2);
      const entry={date,index,mode:twoHumans?'two humans':'human vs computer',moves:0,left:false,referredGuests:0};
      report.current=entry;save();
      try {
        for(let p=0;p<(twoHumans?2:1);p++) {
          const context=await browser.newContext({locale:'en-US',timezoneId:'UTC'});contexts.push(context);
          const page=await context.newPage();pages.push(page);
          page.on('pageerror',error=>report.pageErrors.push({index,message:error.message}));
          page.on('dialog',dialog=>dialog.accept());
          page.on('response',response=>{if(response.status()>=500) report.httpErrors.push({index,status:response.status(),path:new URL(response.url()).pathname});});
          const inviter=p>0?users[0]?.id:previousGuest;
          const referred=inviter && (config.friendProbability??0)>0 && random()<config.friendProbability;
          const portalUrl=new URL('/static/portal.html',base);
          if(referred) {portalUrl.searchParams.set('ref',inviter);entry.referredGuests++;}
          await page.goto(portalUrl.href);
          await click(page.getByRole('button',{name:'Log in',exact:true}));
          await click(page.locator('.auth-menu-panel').getByRole('button',{name:'Log in',exact:true}));
          await page.getByRole('dialog').getByLabel('Guest name').fill(`Portal ${config.runId} ${index} ${p}`);
          await click(page.getByRole('dialog').getByRole('button',{name:'Continue as guest',exact:true}));
          await expect(page.getByRole('dialog')).toBeHidden();
          users.push(await page.evaluate(()=>JSON.parse(localStorage.getItem('game-platform.accounts.v1'))[0]));
          previousGuest=users.at(-1).id;
          if(config.benchmarkHeadersFile && index===0 && p===0) {
            fs.writeFileSync(config.benchmarkHeadersFile,JSON.stringify({'X-User-Id':users[0].id,'X-User-Password':users[0].password}),{mode:0o600});
          }
        }
        const owner=pages[0], game=games[index%games.length];
        await owner.goto(new URL(`/static/portal.html?game=${encodeURIComponent(game.id)}`,base).href);
        const created=owner.waitForResponse(r=>new URL(r.url()).pathname==='/matches'&&r.request().method()==='POST');
        created.catch(()=>{});
        await click(owner.getByRole('button',{name:/Create match/}));
        const createdResponse=await created;
        if(!createdResponse.ok()) throw new Error(`Create match: ${createdResponse.status()}`);
        const match=await createdResponse.json();entry.matchId=match.id;
        if(twoHumans) {
          await pages[1].goto(new URL(`/static/portal.html#match=${match.id}`,base).href);
          await click(pages[1].getByRole('button',{name:'Join',exact:true}));
        } else await click(owner.getByRole('button',{name:'Add computer opponent',exact:true}));
        const started=owner.waitForResponse(r=>new URL(r.url()).pathname===`/matches/${match.id}/start`);
        started.catch(()=>{});
        await click(owner.getByRole('button',{name:'Start game',exact:true}));
        const startResponse=await started;
        if(!startResponse.ok()) throw new Error(`Start match: ${startResponse.status()}`);
        let state=await startResponse.json();
        for(let move=0;move<20 && state.status!=='over';move++) {
          if(leave && move===2) {
            const leaver=pages[twoHumans?1:0];
            if(!await leaver.getByRole('button',{name:'Leave',exact:true}).isVisible()) {
              await click(leaver.getByRole('button',{name:'Controls',exact:true}));
            }
            const ended=leaver.waitForResponse(r=>new URL(r.url()).pathname===`/matches/${match.id}/playerLeft`);
            ended.catch(()=>{});
            await click(leaver.getByRole('button',{name:'Leave',exact:true}));
            const response=await ended;
            if(!response.ok()) throw new Error(`Leave match: ${response.status()}`);
            state=await response.json();entry.left=true;break;
          }
          let active;
          await expect.poll(async()=>{
            const response=await owner.request.get(new URL(`/matches/${match.id}`,base).href,{headers:{'X-User-Id':users[0].id,'X-User-Password':users[0].password}});
            if(!response.ok()) throw new Error(`Read match: ${response.status()}`);
            state=await response.json();
            if(state.status==='over') {active=null;return true;}
            const player=state.players[state.turn_of_player_indices[0]];
            const p=users.findIndex(user=>user.id===player.user_id);
            if(p<0) return false;
            const board=pages[p].frameLocator('#game-box iframe').locator('#board button');
            if(await board.count()!==9) return false;
            const filled=await board.evaluateAll(buttons=>buttons.filter(button=>button.textContent.trim()).length);
            if(filled<state.move_count) return false;
            if(await pages[p].frameLocator('#game-box iframe').locator('#board button:enabled').count()) {active=pages[p];return true;}
            return false;
          },{timeout:20000}).toBe(true);
          if(!active) {state.status='over';break;}
          const board=active.frameLocator('#game-box iframe').locator('#board button:enabled');
          const index=Math.floor(random()*await board.count());
          const moved=active.waitForResponse(r=>new URL(r.url()).pathname===`/matches/${match.id}/moves`&&r.request().method()==='POST');
          moved.catch(()=>{});
          await click(board.nth(index));
          const response=await moved;
          if(!response.ok()) throw new Error(`Move rejected: ${response.status()}`);
          state=await response.json();entry.moves++;
        }
        await expect(owner.locator('#match-status')).toHaveText('over',{timeout:20000});
        entry.status='over';report.completed.push(entry);report.current=null;save();
      } catch(error) {
        if(pages[0]) {
          entry.url=pages[0].url();
          entry.pageText=(await pages[0].locator('body').innerText().catch(()=>'' )).slice(0,3000);
          await pages[0].screenshot({path:reportPath+'.failure.png',fullPage:true}).catch(()=>{});
        }
        report.failures.push({...entry,error:error.message});exitCode=1;save();throw error;
      } finally {for(const context of contexts) await context.close();}
    }
  }
} catch(error) {process.stderr.write(error.message+'\n');exitCode=1;}
finally {
  report.after=await admin('GET','/workload/counts').catch(()=>null);
  report.finishedAt=new Date().toISOString();
  report.success=exitCode===0&&report.completed.length===days*perDay&&report.pageErrors.length===0&&report.httpErrors.length===0;
  save();await browser.close();
}
process.stdout.write(JSON.stringify({success:report.success,matches:report.completed.length,actions:report.actions,
  failures:report.failures.length,pageErrors:report.pageErrors.length,httpErrors:report.httpErrors.length,report:reportPath})+'\n');
process.exitCode=report.success?0:1;
