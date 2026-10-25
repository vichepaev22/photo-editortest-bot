import assert from 'node:assert/strict';
import test from 'node:test';
import {welcomeDue, RETURN_AFTER_MS} from '../motion/welcome/src/welcome-policy.mjs';

test('first entry and explicit replay show the introduction',()=>{
  assert.equal(welcomeDue({seen:false,now:100}),true);
  assert.equal(welcomeDue({seen:true,lastVisit:100,now:101,force:true,closedInSession:true}),true);
});
test('recent and active session visits do not repeat it',()=>{
  const now=2*RETURN_AFTER_MS;
  assert.equal(welcomeDue({seen:true,lastVisit:now-RETURN_AFTER_MS+1,now}),false);
  assert.equal(welcomeDue({seen:true,lastVisit:1,now,closedInSession:true}),false);
});
test('seven days of absence enables a return visit',()=>{
  assert.equal(welcomeDue({seen:true,lastVisit:10,now:10+RETURN_AFTER_MS}),true);
  assert.equal(welcomeDue({seen:true,lastVisit:10+RETURN_AFTER_MS,now:10+RETURN_AFTER_MS+1}),false);
});
test('legacy seen flag or invalid/future clock does not force all existing users through intro',()=>{
  for(const lastVisit of [null,undefined,NaN,Infinity,-1,0,99999]){
    assert.equal(welcomeDue({seen:true,lastVisit,now:100}),false);
  }
});
