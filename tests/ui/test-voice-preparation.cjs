'use strict';
const assert=require('node:assert/strict');
const {fixture,settle,deferred,response}=require('./test-app.cjs');
const voice=()=>({stop(){},suspend(){},async unlock(){}});
const posts=h=>h.requests.filter(r=>r.url==='/api/jobs');
const answers=h=>h.elements.messages.children.filter(r=>r.querySelector('.text'));
async function start(){const h=await fixture({voice:voice(),autoVoicePrepare:false});await h.run('selectVoice(true)');await h.elements.dictate.click();await settle();return h;}
async function ready(h,id=h.run('job')){h.jobs.set(id,{state:'done',action:'voice_prepare',seconds:2,result:{voice_ready:true,model:'test'}});await h.run(`pollJob('${id}')`);await settle();}
(async()=>{
 const h=await start(),id=h.run('job');
 assert.equal(posts(h).length,1);assert.deepEqual(posts(h)[0].body,{mode:'voice_prepare',prompt:''});
 assert.equal(h.run('microphoneOn'),false);assert.equal(h.run('dictationJob'),null);
 assert.match(h.elements.voiceState.textContent,/Prepar|Loading/);assert.equal(h.elements.dictate.textContent,'End voice chat');
 assert.equal(answers(h).length,0,'preparation never invents a conversation');
 assert(h.timers.some(t=>t.delay===125),'own visible voice preparation uses short polling');
 h.context.performance.now=()=>2000;await h.run(`pollJob('${id}')`);await settle();
 assert.equal(h.requests.filter(r=>r.url==='/api/voice/pulse').length,1);
 await ready(h);assert.equal(posts(h).length,2);assert.equal(posts(h)[1].body.mode,'dictate');
 assert.equal(answers(h).length,0);
 h.run("showVoiceTimings({endpoint_silence_audio_seconds:.832,transcription_seconds:1.23,private_transcript:'hidden'})");
 assert.equal(h.elements.voiceElapsed.textContent,'Pause 0.8 s · Transcription 1.2 s');
 for(const reason of ['End','hidden','lock']){
  const x=await start(),old=x.run('job');
  if(reason==='End')await x.elements.dictate.click();
  if(reason==='hidden'){x.context.document.hidden=true;for(const cb of x.documentEvents.visibilitychange)cb();}
  if(reason==='lock')x.elements.ownerLock.dispatch('click');
  await settle();assert(x.requests.some(r=>r.url==='/api/cancel'&&r.body.id===old));
  await ready(x,old);assert.equal(posts(x).length,1,reason+' prevents stale ready microphone start');
  assert.equal(x.run('voiceChat'),false);assert.equal(answers(x).length,0);
 }
 const failed=await start(),fid=failed.run('job');failed.jobs.set(fid,{state:'error',action:'voice_prepare',seconds:1,error:'Not enough memory'});
 await failed.run(`pollJob('${fid}')`);await settle();assert.equal(posts(failed).length,1);assert.equal(failed.run('voiceChat'),false);assert.match(failed.elements.notice.textContent,/Not enough memory/);
 const pending=await fixture({voice:voice(),autoVoicePrepare:false});await pending.run('selectVoice(true)');const hold=deferred();pending.server.pendingPost=hold;
 const starting=pending.elements.dictate.click();await settle();await pending.elements.dictate.click();await settle();
 pending.jobs.set('late',{state:'done',action:'voice_prepare',result:{voice_ready:true}});hold.resolve(response({id:'late'}));await starting;await settle();
 assert.equal(posts(pending).length,1);assert(pending.requests.some(r=>r.url==='/api/cancel'&&r.body.id==='late'));
 const rejected=await fixture({voice:voice(),autoVoicePrepare:false});await rejected.run('selectVoice(true)');const reject=deferred();rejected.server.pendingPost=reject;
 const rejectedStart=rejected.elements.dictate.click();await settle();await rejected.elements.dictate.click();await settle();reject.reject(Error('request refused'));await rejectedStart;await settle();
 assert.equal(rejected.run('busy'),false,'late rejected acceptance cannot strand the interface after End');assert.equal(rejected.run('voiceChat'),false);
 const observed=await fixture({voice:voice()});observed.run("job='other'");await ready(observed,'other');assert.equal(posts(observed).length,0,'another tab preparation cannot open microphone');
 const dup=await start(),did=dup.run('job'),held=deferred();dup.queued.set('/api/job/'+did,[held.promise]);
 const one=dup.run(`pollJob('${did}')`);await settle();const count=dup.requests.filter(r=>r.url==='/api/job/'+did).length;
 await dup.run(`pollJob('${did}')`);assert.equal(dup.requests.filter(r=>r.url==='/api/job/'+did).length,count,'only one outstanding poll per job');
 held.resolve(response({state:'running',action:'voice_prepare',cancellable:true}));await one;
 console.log('Voice preparation passed: off until ready, failure, End, lock, hidden, late acceptance, other tab, pulse and one-poll ownership.');
})().catch(e=>{console.error(e);process.exitCode=1;});
