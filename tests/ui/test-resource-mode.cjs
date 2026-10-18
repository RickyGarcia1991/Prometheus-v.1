'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {fixture,settle,response,deferred}=require('./test-app.cjs');
const preference={requested:'Balanced',effective:'Battery Saver',reason:'Battery reserve takes precedence.'};
async function ready(){const h=await fixture();h.statusData.resource_status={mode_preference:preference};await h.run('status()');return h;}
test('saved and effective modes are distinct; polling preserves an unsaved selection',async()=>{
 const h=await ready(),e=h.elements;
 assert.equal(e.resourceMode.value,'Balanced');assert.match(e.resourceModeStatus.textContent,/Balanced.*Battery Saver.*Battery reserve/);
 e.resourceMode.value='Performance';e.resourceMode.dispatch('change');await h.run('status()');assert.equal(e.resourceMode.value,'Performance');
 h.server.statusFailure=true;await h.run('status()');assert.equal(e.resourceMode.disabled,true);assert.equal(e.saveResourceMode.disabled,true);assert.match(e.resourceModeStatus.textContent,/not current/);
});
test('failed save cannot claim success or discard the unsaved selection',async()=>{
 const h=await ready(),e=h.elements;e.resourceMode.value='Idle';e.resourceMode.dispatch('change');
 h.queued.set('/api/resources/preference',[Promise.resolve(response({error:'Storage unavailable'},400))]);
 e.saveResourceMode.dispatch('click');await settle();
 assert.match(e.resourceModeStatus.textContent,/not confirmed.*Storage unavailable/);assert.equal(e.resourceMode.value,'Idle');
 await h.run('status()');assert.equal(e.resourceMode.value,'Idle');
});
test('save uses exact endpoint and automatic uses null',async()=>{
 const h=await ready(),e=h.elements;e.resourceMode.value='';e.resourceMode.dispatch('change');
 h.queued.set('/api/resources/preference',[Promise.resolve(response({mode_preference:{requested:null,effective:'Balanced',reason:'Automatic'}}))]);
 e.saveResourceMode.dispatch('click');await settle();
 const request=h.requests.findLast(x=>x.url==='/api/resources/preference');assert.deepEqual(request.body,{mode:null});
 assert.match(e.resourceModeStatus.textContent,/Saved preference: Automatic/);assert.equal(e.resourceMode.dataset.edited,undefined);
});
test('pending save does not send a second mutation during status polling',async()=>{
 const h=await ready(),e=h.elements,waiting=deferred();
 h.queued.set('/api/resources/preference',[waiting.promise]);e.saveResourceMode.dispatch('click');await settle();await h.run('status()');
 e.saveResourceMode.dispatch('click');await settle();assert.equal(h.requests.filter(x=>x.url==='/api/resources/preference').length,1);
 waiting.resolve(response({mode_preference:preference}));await settle();
});
test('in-flight save disables selection through polling and restores it after completion',async()=>{
 const h=await ready(),e=h.elements,waiting=deferred();e.resourceMode.value='Idle';e.resourceMode.dispatch('change');
 h.queued.set('/api/resources/preference',[waiting.promise]);e.saveResourceMode.dispatch('click');await settle();
 assert.equal(e.resourceMode.disabled,true);assert.equal(e.saveResourceMode.disabled,true);
 await h.run('status()');assert.equal(e.resourceMode.disabled,true);assert.equal(e.saveResourceMode.disabled,true);assert.equal(e.resourceMode.value,'Idle');
 waiting.resolve(response({mode_preference:{requested:'Idle',effective:'Idle',reason:'Saved'}}));await settle();
 assert.equal(e.resourceMode.value,'Idle');assert.equal(e.resourceMode.disabled,false);assert.equal(e.saveResourceMode.disabled,false);
});
