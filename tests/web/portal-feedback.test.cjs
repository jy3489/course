const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createTracker} = require('../../static/portal-feedback.js');
const match = (count, status='ongoing', id='a') => ({id,move_count:count,status});
test('only new turn and confirmed results produce cues, never polling or first load', () => {
 const update=createTracker();
 assert.equal(update(match(0),'u',{yourTurn:false}),null);
 assert.equal(update(match(1),'u',{yourTurn:true}),'turn');
 assert.equal(update(match(1),'u',{yourTurn:true}),null);
 assert.equal(update(match(2,'over'),'u',{outcome:'win'}),'win');
 assert.equal(update(match(2,'over'),'u',{outcome:'win'}),null);
});
test('history, offline, reconnect, switching accounts and matches stay silent', () => {
 const update=createTracker();
 update(match(1),'u',{});
 assert.equal(update(match(2),'u',{suppressed:true,yourTurn:true}),null);
 assert.equal(update(match(3),'u',{yourTurn:true}),null);
 assert.equal(update(match(4),'v',{yourTurn:true}),null);
 assert.equal(update(match(5,'over','b'),'v',{outcome:'win'}),null);
});
test('start and all explicit result cues; unknown results are silent', () => {
 for (const outcome of ['win','draw','loss','neutral']) {
  const update=createTracker();
  update(match(0,'waiting_for_players'),'u');
  assert.equal(update(match(0),'u'),'start');
  assert.equal(update(match(1,'over'),'u',{outcome}),outcome==='neutral'?null:outcome);
 }
});
