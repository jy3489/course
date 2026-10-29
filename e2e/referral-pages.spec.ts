import {test, expect} from '@playwright/test';

test('query-mode referrals browse branches and search without a global export',async({page})=>{
  const errors:string[]=[];let legacy=0;
  page.on('pageerror',error=>errors.push(error.message));
  const alice={id:'alice',display_name:'Alice',total_invites:2,direct_invites:1,invited_by_user_id:null};
  const bob={id:'bob',display_name:'Bob',total_invites:1,direct_invites:1,invited_by_user_id:'alice',invited_by_name:'Alice'};
  const carol={id:'carol',display_name:'Carol',total_invites:0,direct_invites:0,invited_by_user_id:'bob',invited_by_name:'Bob'};
  await page.route('**/storage/capabilities',route=>route.fulfill({json:{pagination:true}}));
  await page.route('**/admin/invites',route=>{legacy++;return route.fulfill({status:500});});
  await page.route('**/v2/admin/referrals/summary',route=>route.fulfill({json:{summary:{users:3,invited_users:2,inviters:2,biggest_tree:3,longest_chain:2,cycles_broken:0},generated_at:'2026-09-30T12:00:00Z'}}));
  await page.route('**/v2/admin/referrals/users?*',route=>{
    const query=new URL(route.request().url()).searchParams;
    let items=[alice,bob,carol];
    if(query.has('parent_id')) items=query.get('parent_id')==='alice'?[bob]:query.get('parent_id')==='bob'?[carol]:[alice];
    if(query.get('q')) items=items.filter(user=>user.id.startsWith(query.get('q')!.toLowerCase()));
    return route.fulfill({json:{items,next_cursor:null}});
  });
  await page.goto('/invites');
  await page.locator('#leaderboard').getByRole('button',{name:'Alice',exact:true}).click();
  await expect(page.locator('#detail')).toContainText('2 total invited');
  await page.locator('#detail').getByRole('button',{name:'Bob',exact:true}).click();
  await expect(page.locator('#detail')).toContainText('Carol');
  await page.locator('#detail').getByRole('button',{name:'Invited by Alice'}).click();
  await expect(page.locator('#detail h3')).toHaveText('Alice');
  await page.locator('#search').fill('Bob');
  await expect(page.locator('#results')).toContainText('Bob');
  expect(legacy).toBe(0);expect(errors).toEqual([]);
});
