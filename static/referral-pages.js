"use strict";
// Query-mode referral snapshots: page through branches rather than downloading
// every account and recursively building the entire tree in the browser.
window.ReferralPages=(()=>{
  let supported, timer;
  const $=id=>document.getElementById(id);
  async function get(path){const response=await fetch(path);if(!response.ok)throw new Error(`${path}: ${response.status}`);return response.json();}
  async function enabled(){if(supported===undefined)supported=(await get('/storage/capabilities')).pagination===true;return supported;}
  const table=items=>el('table',{},el('thead',{},el('tr',{},['User','Total invited','Direct'].map(textContent=>el('th',{textContent})))),
    el('tbody',{},items.map(user=>el('tr',{},el('td',{},el('button',{type:'button',textContent:user.display_name,onclick:()=>detail(user).catch(fail)})),el('td',{textContent:user.total_invites}),el('td',{textContent:user.direct_invites})))));
  function fail(error){$('error').textContent=error.message;}
  async function listing(container, params){
    const history=[];let cursor=null;
    async function load(){
      const query=new URLSearchParams({...params,limit:'50'});if(cursor)query.set('cursor',cursor);
      const result=await get('/v2/admin/referrals/users?'+query);
      const back=el('button',{type:'button',textContent:'Previous page',disabled:!history.length,onclick:()=>{cursor=history.pop();load().catch(fail);}});
      const next=el('button',{type:'button',textContent:'Next page',disabled:!result.next_cursor,onclick:()=>{history.push(cursor);cursor=result.next_cursor;load().catch(fail);}});
      container.replaceChildren(table(result.items),el('nav',{'aria-label':'Referral pages'},back,next));
    }
    await load();
  }
  async function detail(user){
    const children=el('div',{});
    $('detail').replaceChildren(el('h3',{textContent:user.display_name}),el('p',{textContent:`${user.total_invites} total invited; ${user.direct_invites} directly invited.`}),el('h4',{textContent:'Direct invitees'}),children);
    if(user.invited_by_user_id) $('detail').prepend(el('button',{type:'button',textContent:`Invited by ${user.invited_by_name || user.invited_by_user_id}`,onclick:async()=>{try{const result=await get('/v2/admin/referrals/users?'+new URLSearchParams({q:user.invited_by_user_id}));if(result.items[0])await detail(result.items[0]);}catch(error){fail(error);}}}));
    await listing(children,{parent_id:user.id});
  }
  async function refresh(){
    const result=await get('/v2/admin/referrals/summary');
    $('error').textContent='';
    $('freshness').textContent=`Report built ${formatTime(result.generated_at)}`;
    $('summary').replaceChildren(el('div',{class:'cards'},Object.entries(result.summary).map(([label,value])=>el('div',{class:'card'},el('div',{class:'value',textContent:value}),el('div',{textContent:label.replaceAll('_',' ')})))));
    $('search').placeholder='Name prefix, full user ID, or referral code';
    $('search').setAttribute('aria-label','Name prefix, full user ID, or referral code');
    $('detail').replaceChildren();$('results').replaceChildren();
    await Promise.all([listing($('leaderboard'),{}),listing($('tree'),{parent_id:''})]);
  }
  function search(){clearTimeout(timer);timer=setTimeout(()=>{const q=$('search').value.trim();if(!q){$('results').replaceChildren();return;}listing($('results'),{q}).catch(fail);},250);}
  return {enabled,active:()=>supported===true,refresh,search};
})();
