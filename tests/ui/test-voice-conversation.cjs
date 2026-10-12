'use strict';
const assert = require('node:assert/strict');
const {fixture, settle, deferred, response} = require('./test-app.cjs');
const posts = h => h.requests.filter(r => r.url === '/api/jobs');
function fakeVoice(){return {played:[],stop(){},suspend(){},async unlock(){},level(){return .5;},async play(text,api,start,end){this.played.push(text);this.end=end;start();return true;}};}
async function finish(h, result, id=h.run('job')){h.jobs.set(id,{state:'done',seconds:12,result});await h.run(`pollJob('${id}')`);await settle();}

(async()=>{
 const one=await fixture();one.elements.prompt.value='My existing draft';
 await one.elements.dictate.click();await settle();const id=one.run('dictationJob');assert(id);
 one.context.performance.now=()=>5000;
 one.jobs.set(id,{state:'running',recording:true,level:.8,elapsed:18.4,cancellable:true,stage:'Listening'});
 await one.run(`pollJob('${id}')`);await settle();
 assert.equal(one.elements.voiceElapsed.textContent,'0:18','recording continues beyond eight seconds');
 assert.equal(one.elements.finishRecording.hidden,false);
 assert.equal(one.elements.dictate.disabled,false,'cancel remains available while recording');
 assert(one.elements.voiceWave.children.some(b=>parseFloat(b.style.height)>20),'wave responds to actual input level');
 assert.equal(one.requests.filter(r=>r.url==='/api/dictation/pulse').length,1);
 await one.elements.finishRecording.click();await settle();
 assert.equal(one.requests.findLast(r=>r.url==='/api/dictation/finish').body.id,id);
 assert.equal(one.requests.filter(r=>r.url==='/api/cancel').length,0,'finish retains audio for transcription');
 await finish(one,{transcript:'these are my spoken words'});
 assert.equal(one.elements.prompt.value,'My existing draft\nthese are my spoken words');
 assert.equal(posts(one).length,1,'ordinary dictation is held for review');
 assert(one.elements.voiceCapture.hidden);

 for(const handsFree of [false,true]){
  const empty=await fixture({voice:fakeVoice()});
  if(handsFree)await empty.run('selectVoice(true)');
  await empty.elements.dictate.click();await settle();const emptyId=empty.run('dictationJob');
  empty.elements.prompt.value='Preserve this draft';
  empty.jobs.set(emptyId,{state:'cancelled',action:'dictate',reason:'empty_recording',seconds:1,
   error:'No audible speech detected. Microphone off; nothing was submitted.',recording:false});
  await empty.run(`pollJob('${emptyId}')`);await settle();
  assert.equal(empty.elements.orb.dataset.state,'idle','empty Stop recording is not a red device failure');
  assert.equal(empty.run('voiceChat'),false);assert.equal(empty.run('dictationJob'),null);
  assert.equal(empty.run('busy'),false);assert(empty.elements.voiceCapture.hidden);
  assert.equal(empty.elements.messages.children.length,0,'empty recording creates no error bubble or conversation');
  assert.equal(empty.elements.prompt.value,'Preserve this draft');
  assert.match(empty.elements.notice.textContent,/No audible speech.*Microphone off/);
  await empty.run('listenAfterReply()');await settle();
  assert.equal(posts(empty).length,1,'empty hands-free input neither submits an empty message nor restarts recording');
 }
 const failed=await fixture({voice:fakeVoice()});await failed.run('selectVoice(true)');await failed.elements.dictate.click();await settle();
 const failedId=failed.run('dictationJob');failed.jobs.set(failedId,{state:'error',action:'dictate',seconds:1,error:'Microphone device failed',recording:false});
 await failed.run(`pollJob('${failedId}')`);await settle();
 assert.equal(failed.elements.orb.dataset.state,'error','genuine microphone failures still need attention');
 assert.equal(failed.run('voiceChat'),false);assert.equal(posts(failed).length,1);

 const racing=await fixture();await racing.elements.dictate.click();await settle();const raceId=racing.run('dictationJob');
 racing.context.performance.now=()=>5000;
 racing.jobs.set(raceId,{state:'running',recording:true,level:.4,elapsed:10});
 racing.queued.set('/api/dictation/pulse',[response({error:'This dictation is no longer recording.'},400)]);
 await racing.run(`pollJob('${raceId}')`);await settle();
 assert.equal(racing.run('dictationJob'),raceId,'a pulse racing with silence detection must not cancel transcription');
 assert.equal(racing.requests.filter(r=>r.url==='/api/cancel').length,0);
 await finish(racing,{transcript:'My finished thought'});assert.equal(racing.elements.prompt.value,'My finished thought');

 const output=fakeVoice(),chat=await fixture({voice:output});
 await chat.run('selectVoice(true)');await chat.elements.dictate.click();await settle();
 assert(chat.run('voiceChat'));assert.equal(chat.elements.dictate.textContent,'End voice chat');
 chat.elements.mode.value='home';chat.elements.homeKey.value='must-not-be-sent';
 await finish(chat,{transcript:'What is six times seven?'});
 assert.equal(posts(chat).length,2);assert.equal(posts(chat).at(-1).body.mode,'chat');
 assert.equal(posts(chat).at(-1).body.home_key,undefined,'hands-free speech never dispatches a Home command');
 assert.equal(posts(chat).at(-1).body.prompt,'What is six times seven?');
 await finish(chat,{reply:'Forty two',session:'b'.repeat(32),label:'Local helper'});
 assert.deepEqual(output.played,['Forty two']);assert.equal(posts(chat).length,2,'microphone stays off throughout playback');
 output.end();await settle();assert.equal(posts(chat).length,3,'only finished playback starts next listening turn');
 assert.equal(posts(chat).at(-1).body.mode,'dictate');
 const secondCapture=chat.run('dictationJob');await chat.elements.dictate.click();await settle();
 assert.equal(chat.run('voiceChat'),false);assert.equal(chat.run('spokenMode'),false);
 assert.equal(chat.requests.findLast(r=>r.url==='/api/cancel').body.id,secondCapture);
 await finish(chat,{transcript:'late words after ending'},secondCapture);
 assert.equal(posts(chat).length,3);assert.equal(chat.elements.prompt.value,'','late discarded transcript is never sent or restored');
 assert.equal(chat.elements.notice.textContent,'Voice chat ended. Microphone off.');
 output.end();await settle();assert.equal(posts(chat).length,3,'late audio callback cannot restart microphone');

 const waiting=await fixture({voice:fakeVoice()});await waiting.run('selectVoice(true)');await waiting.elements.dictate.click();await settle();
 await finish(waiting,{transcript:'Hello'});await finish(waiting,{reply:'Hi there'});
 waiting.run('busy=true; listenAfterReply()');assert.equal(posts(waiting).length,2);
 const retry=waiting.timers.findLast(t=>t.delay===500);assert(retry,'maintenance does not strand a hands-free session');
 waiting.run('busy=false');retry.fn();await settle();assert.equal(posts(waiting).length,3);
 await waiting.elements.dictate.click();retry.fn();await settle();assert.equal(posts(waiting).length,3,'End invalidates a waiting turn');

 const hidden=await fixture({voice:fakeVoice()});await hidden.run('selectVoice(true)');await hidden.elements.dictate.click();await settle();
 const hiddenId=hidden.run('dictationJob');hidden.context.document.hidden=true;
 for(const callback of hidden.documentEvents.visibilitychange)callback();await settle();
 assert.equal(hidden.run('voiceChat'),false);assert.equal(hidden.requests.findLast(r=>r.url==='/api/cancel').body.id,hiddenId);
 hidden.context.document.hidden=false;for(const callback of hidden.documentEvents.visibilitychange)callback();
 assert.equal(posts(hidden).length,1,'returning to a tab does not re-arm the microphone');
 await finish(hidden,{transcript:'discarded while hidden'},hiddenId);
 assert.match(hidden.elements.notice.textContent,/tab was hidden/,'the reason remains visible after cancellation completes');

 const pending=await fixture({voice:fakeVoice()});await pending.run('selectVoice(true)');
 const acceptance=deferred();pending.server.pendingPost=acceptance;
 const start=pending.elements.dictate.click();await settle();await pending.elements.dictate.click();await settle();
 pending.jobs.set('late-capture',{state:'running',recording:true});acceptance.resolve(response({id:'late-capture'}));await start;await settle();
 assert.equal(pending.run('voiceChat'),false);assert.equal(pending.run('dictationJob'),null);
 assert(pending.requests.some(r=>r.url==='/api/cancel'&&r.body.id==='late-capture'),'end during pending start cancels late acceptance');
 await finish(pending,{transcript:'discarded pending input'},'late-capture');
 assert.equal(pending.elements.prompt.value,'');
 assert.equal(pending.elements.notice.textContent,'Voice chat ended. Microphone off.');

 const draft=await fixture({voice:fakeVoice()});await draft.run('selectVoice(true)');draft.elements.prompt.value='Do not discard my draft';
 await draft.elements.dictate.click();assert.equal(posts(draft).length,0);assert.equal(draft.elements.prompt.value,'Do not discard my draft');
 const editing=await fixture({voice:fakeVoice()});await editing.run('selectVoice(true)');await editing.elements.dictate.click();await settle();
 editing.elements.prompt.value='I am still editing';await finish(editing,{transcript:'my spoken words'});
 assert.equal(posts(editing).length,1,'editing during recording holds both draft and transcript for review');
 assert.equal(editing.elements.prompt.value,'I am still editing\nmy spoken words');assert.equal(editing.run('voiceChat'),false);
 const long=await fixture({voice:fakeVoice()});await long.run('selectVoice(true)');await long.elements.dictate.click();await settle();
 await finish(long,{transcript:'a'.repeat(4001)});assert.equal(long.run('voiceChat'),false);assert.equal(posts(long).length,1);assert.equal(long.elements.prompt.value.length,4001,'long transcript preserved, never truncated');
 const observed=await fixture();observed.run("job='someone-elses-recording'");
 observed.jobs.set('someone-elses-recording',{state:'running',recording:true,level:1,elapsed:12});
 await observed.run("pollJob('someone-elses-recording')");assert(!observed.requests.some(r=>r.url==='/api/dictation/pulse'));
 await finish(observed,{transcript:'Another tab'},'someone-elses-recording');assert.equal(observed.elements.prompt.value,'');
 console.log('Voice conversation passed: long recording, measured waveform, manual finish, lease, review, hands-free turns, playback gating, End, hidden tab, delayed acceptance, draft preservation, long transcript and other-tab isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
