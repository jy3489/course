import { test, expect } from '@playwright/test';
import { createGame, createUser, loginAs, uniqueName } from './helpers';

test('v2 practice works against a computer after disconnect and reload, with no match writes', async ({page,request,context})=>{
  const owner=await createUser(request,uniqueName('Offline owner'));
  const name=uniqueName('Offline noughts');
  await createGame(request,owner,name);
  await loginAs(page,owner);
  const writes:string[]=[];
  page.on('request',req=>{if(req.method()!=='GET' && new URL(req.url()).pathname.startsWith('/matches'))writes.push(req.url());});
  await page.goto('/static/portal.html');
  await page.getByRole('combobox',{name:'Search games'}).fill(name);
  await page.getByRole('option',{name:new RegExp(name)}).click();
  await expect(page.locator('#practice')).toBeEnabled();
  await page.locator('#practice').click();
  const board=page.frameLocator('#game-box iframe').locator('#board button');
  await expect(board).toHaveCount(9);
  await board.first().click();
  await expect(board.filter({hasText:'X'})).toHaveCount(1);
  await expect(board.filter({hasText:'O'})).toHaveCount(1);
  await page.evaluate(()=>navigator.serviceWorker.ready.then(()=>undefined));
  await expect.poll(()=>page.evaluate(()=>Boolean(navigator.serviceWorker.controller))).toBe(true);
  await context.setOffline(true);
  await page.reload();
  await expect(board.filter({hasText:'X'})).toHaveCount(1);
  await expect(board.filter({hasText:'O'})).toHaveCount(1);
  await board.filter({hasText:/^$/}).first().click();
  await expect(board.filter({hasText:'X'})).toHaveCount(2);
  await expect(board.filter({hasText:'O'})).toHaveCount(2);
  await page.locator('#match-controls-toggle').click();
  await page.locator('#history-back').click();
  await expect(page.locator('#history-position')).toContainText('read only');
  await expect(page.frameLocator('#game-box iframe').locator('#board button:enabled')).toHaveCount(0);
  await page.locator('#history-live').click();
  await expect(page.locator('#history-position')).toContainText('Live');
  await context.setOffline(false);
  await expect(page.locator('#match')).toBeVisible();
  expect(writes).toEqual([]);
});

test('local rules have no portal storage or network access, and a looping game times out',async({page})=>{
  await page.goto('/static/portal.html');
  const result=await page.evaluate(async()=>{
    const runner=(window as any).PortalPractice ?? PortalPractice;
    const match={players:[],move_count:0,practiceSeed:1,created_at:new Date().toISOString()};
    const isolated=await runner.call({server_code:`const game={newMatch(){return {storage:typeof localStorage,document:typeof document};}};`},'newMatch',[],match);
    const blocked=await runner.call({server_code:`const game={newMatch(){try {const xhr=new XMLHttpRequest();xhr.open('GET','https://example.com/',false);xhr.send();return false;}catch{return true;}}};`},'newMatch',[],match);
    let timeout='';try {await runner.call({server_code:'while(true){}'},'newMatch',[],match);} catch(e){timeout=(e as Error).message;}
    return {isolated,blocked,timeout};
  });
  expect(result.isolated).toEqual({storage:'undefined',document:'undefined'});
  expect(result.blocked).toBe(true);
  expect(result.timeout).toContain('too long');
});

test('saved local practice can move while storage metadata is unresponsive',async({page,request})=>{
  const owner=await createUser(request,uniqueName('Local owner'));
  const name=uniqueName('Local responsive');
  await createGame(request,owner,name);
  await loginAs(page,owner);
  await page.goto('/static/portal.html');
  await page.getByRole('combobox',{name:'Search games'}).fill(name);
  await page.getByRole('option',{name:new RegExp(name)}).click();
  await page.locator('#practice').click();
  const board=page.frameLocator('#game-box iframe').locator('#board button');
  await expect(board).toHaveCount(9);
  await page.route('**/storage/capabilities',()=>new Promise(()=>{}));
  await page.reload();
  await expect(board).toHaveCount(9);
  await board.first().click();
  await expect(board.filter({hasText:'X'})).toHaveCount(1);
  await expect(board.filter({hasText:'O'})).toHaveCount(1);
});
