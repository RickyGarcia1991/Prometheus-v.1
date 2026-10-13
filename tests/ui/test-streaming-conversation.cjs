'use strict';
const assert=require('node:assert/strict');
const {fixture,settle,deferred,response}=require('./test-app.cjs');
const {LocalVoice}=require('../../src/prometheus_assistant/ui_assets/voice-player.js');
// Count conversational operations separately from content-free preparation.
const posts=h=>h.requests.filter(r=>r.url==='/api/jobs'&&r.body.mode!=='voice_prepare');
function fakeVoice(){return {
 streams:[],played:[],stop(){},suspend(){},async unlock(){},level(){return .4;},
 async play(text,_request,start,end){this.played.push(text);this.end=end;start();},
 stream(request,start,end,fail){const handle={updates:[],cancelled:0,start,end,fail,request:text=>request('/api/speech',{text}),
  update(text,done){this.updates.push({text,done});return true;},cancel(){this.cancelled++;}};this.streams.push(handle);return handle;},
};}
async function start({handsFree=false,voice=fakeVoice()}={}){
 const h=await fixture({voice});await h.run('selectVoice(true)');
 if(handsFree){await h.elements.dictate.click();await settle();const capture=h.run('job');h.jobs.set(capture,{state:'done',seconds:1,result:{transcript:'What is six times seven?'}});await h.run(`pollJob('${capture}')`);}
 else{h.elements.prompt.value='What is six times seven?';await h.run("submit('chat')");}
 await settle();return {h,voice,id:h.run('job')};
}
async function running(h,id,text='The answer is forty-two. More',epoch=1){h.jobs.set(id,{state:'running',stage:'Generating',partial:text,cancellable:true,speech_streamable:true,speech_epoch:epoch});await h.run(`pollJob('${id}')`);await settle();}
async function finish(h,id,text='The answer is forty-two. More explanation.',epoch=1){h.jobs.set(id,{state:'done',seconds:2,speech_streamable:true,speech_epoch:epoch,result:{reply:text,label:'Local chat'}});await h.run(`pollJob('${id}')`);await settle();}
(async()=>{
 const {h,voice,id}=await start({handsFree:true});assert.equal(posts(h).at(-1).body.voice_reply,true);
 assert.equal(posts(h)[0].body.voice_reply,undefined,'recording is never marked as a voice reply');
 await running(h,id);assert.equal(voice.streams.length,1);const first=voice.streams[0];
 await first.request('The answer is forty-two.');
 assert.deepEqual(h.requests.findLast(r=>r.url==='/api/speech').body,{text:'The answer is forty-two.',job_id:id,speech_epoch:1});
 first.start();await settle();assert.equal(h.run('speaking'),true);
 assert(h.elements.messages.children.some(e=>e.querySelector('.label')?.textContent==='In progress · not saved'),'spoken provisional text is still labelled unsaved');
 first.end();await settle();assert.equal(posts(h).length,2,'even an early completion callback cannot reopen microphone before final acceptance');
 await finish(h,id);assert.deepEqual(first.updates.at(-1),{text:'The answer is forty-two. More explanation.',done:true});
 assert.equal(voice.played.length,0,'the accepted answer must not replay its already spoken prefix');
 assert.equal(posts(h).length,2,'final text does not reopen microphone while audio remains');
 first.end();await settle();assert.equal(posts(h).length,3);assert.equal(posts(h).at(-1).body.mode,'dictate');
 first.end();await settle();assert.equal(posts(h).length,3,'completion re-arms microphone exactly once');
 assert.equal(h.requests.filter(r=>r.url==='/api/speech/cancel').length,0,'natural completion does not cancel a finished binding');

 const retry=await start({handsFree:true});await running(retry.h,retry.id);const old=retry.voice.streams[0];old.start();
 await running(retry.h,retry.id,'',2);assert.equal(old.cancelled,1);assert.equal(retry.voice.streams.length,2);
 assert.deepEqual(retry.h.requests.findLast(r=>r.url==='/api/speech/cancel').body,{job_id:retry.id,speech_epoch:1});
 const next=retry.voice.streams[1];old.end();old.fail(Error('late old failure'));await settle();
 assert.equal(retry.h.run('voiceChat'),true);assert.equal(posts(retry.h).length,2);
 await running(retry.h,retry.id,'Corrected response. Continuing',2);next.start();await finish(retry.h,retry.id,'Corrected response. Finished.',2);next.end();await settle();
 assert.equal(posts(retry.h).length,3);assert.equal(retry.voice.played.length,0);

 for(const action of ['end','hidden','stop','disconnect','restart','ownerlock','joberror','prefixerror','cancel']){
  const test=await start({handsFree:true});await running(test.h,test.id);const stream=test.voice.streams[0];stream.start();
  if(action==='end')await test.h.elements.dictate.click();
  if(action==='hidden'){test.h.context.document.hidden=true;for(const callback of test.h.documentEvents.visibilitychange)callback();}
  if(action==='stop')test.h.elements.stopSpeech.onclick();
  if(action==='disconnect'){test.h.queued.set('/api/job/'+test.id,[Promise.reject(Error('Disconnected'))]);await test.h.run(`pollJob('${test.id}')`);}
  if(action==='restart')test.h.run('transport.onRestart()');
  if(action==='ownerlock'){test.h.queued.set('/api/owner/lock',[response({locked:true})]);test.h.elements.ownerLock.click();}
  if(action==='joberror'){test.h.jobs.set(test.id,{state:'error',action:'chat',error:'Generation failed',seconds:1});await test.h.run(`pollJob('${test.id}')`);}
  if(action==='prefixerror')stream.fail(Error('The streamed reply changed.'));
  if(action==='cancel')test.h.elements.cancelRequest.click();
  await settle();stream.end();stream.start();stream.fail(Error('late error'));await settle();
  assert.equal(stream.cancelled,1,action+' cancels the stream');assert.equal(test.h.run('voiceChat'),false,action+' leaves mic off');
  assert.equal(test.h.run('speaking'),false,action+' late callbacks cannot resume audio');
  assert.equal(posts(test.h).length,2,action+' cannot submit another capture');
  assert.deepEqual(test.h.requests.findLast(r=>r.url==='/api/speech/cancel').body,{job_id:test.id,speech_epoch:1});
  if(['end','hidden','stop','ownerlock','cancel'].includes(action))assert(test.h.requests.some(r=>r.url==='/api/cancel'&&r.body.id===test.id),action+' cancels running generation');
  if(test.h.run('job')===test.id){test.h.context.document.hidden=false;await test.h.run('selectVoice(true)');await finish(test.h,test.id);assert.equal(test.voice.played.length,0,action+' cannot replay suppressed final answer');assert.equal(test.voice.streams.length,1);}
 }
 const late=await start({handsFree:true}); // complete this turn so the next controlled submission can be held
 late.h.run('stopVoiceChat(); stopSpeaking(); job=null; localJob=null; setBusy(false)');await late.h.run('selectVoice(true)');late.h.run('voiceChat=true');
 const acceptance=deferred();late.h.server.pendingPost=acceptance;late.h.elements.prompt.value='Next question';const pending=late.h.run("submit('chat')");await settle();
 await late.h.elements.dictate.click();await settle();late.h.jobs.set('late-chat',{state:'running',partial:'Late answer. Next',speech_streamable:true,speech_epoch:1});acceptance.resolve(response({id:'late-chat'}));await pending;await settle();
 assert(late.h.requests.some(r=>r.url==='/api/cancel'&&r.body.id==='late-chat'),'End while job acceptance is pending cancels the accepted generation');
 await late.h.run('selectVoice(true)');await finish(late.h,'late-chat');assert.equal(late.voice.streams.length,0);assert.equal(late.voice.played.length,0);

 const observedVoice=fakeVoice(),observed=await fixture({voice:observedVoice});await observed.run('selectVoice(true)');observed.statusData.job='other-tab';observed.statusData.busy=true;
 observed.jobs.set('other-tab',{state:'running',partial:'Other tab answer. More',speech_streamable:true,speech_epoch:1});await observed.run('status()');await settle();
 await finish(observed,'other-tab');assert.equal(observedVoice.streams.length,0);assert.equal(observedVoice.played.length,0,'another tab never owns this speaker');
 const textOnly=await fixture({voice:fakeVoice()});textOnly.elements.prompt.value='Hello';await textOnly.run("submit('chat')");assert.equal(posts(textOnly).at(-1).body.voice_reply,undefined);
 const finalOnly=await start();await running(finalOnly.h,finalOnly.id,'Unsuitable output',1);finalOnly.h.jobs.set(finalOnly.id,{state:'running',partial:'Formatted output',speech_streamable:false,speech_epoch:2});await finalOnly.h.run(`pollJob('${finalOnly.id}')`);await settle();
 assert.equal(finalOnly.voice.streams[0].cancelled,1,'a revoked streaming eligibility cancels early speech');
 const ordinaryVoice=fakeVoice(),ordinary=await fixture({voice:ordinaryVoice});await ordinary.run('selectVoice(true)');ordinary.elements.prompt.value='2+2';await ordinary.run("submit('calculate')");await settle();
 assert.equal(posts(ordinary).at(-1).body.voice_reply,undefined);const calc=ordinary.run('job');ordinary.jobs.set(calc,{state:'done',seconds:0,result:{reply:'4'}});await ordinary.run(`pollJob('${calc}')`);await settle();assert.deepEqual(ordinaryVoice.played,['4'],'other accepted answers retain the existing playback path');

 // Run actual LocalVoice through app + transport, holding the final job response.
 let audioContext;
 global.AudioContext=class{
  constructor(){audioContext=this;this.state='running';this.currentTime=0;this.destination={};this.sources=[];}
  async resume(){this.state='running';}async suspend(){this.state='suspended';}addEventListener(){}removeEventListener(){}
  async decodeAudioData(){return {sampleRate:16000,duration:1,numberOfChannels:1,getChannelData:()=>new Float32Array(1600)};}
  createBufferSource(){const source={connect(){},disconnect(){},start(){this.started=true;},stop(){},finish(){this.onended?.();}};this.sources.push(source);return source;}
 };
 const real=await start({handsFree:true,voice:new LocalVoice()});await running(real.h,real.id,'The answer is forty-two. More');
 assert.equal(audioContext.sources.length,1);assert(audioContext.sources[0].started,'actual audio begins during running job');
 const finalResponse=deferred();real.h.queued.set('/api/job/'+real.id,[finalResponse.promise]);const finalPoll=real.h.run(`pollJob('${real.id}')`);await settle();
 audioContext.sources[0].finish();await settle();assert.equal(posts(real.h).length,2,'finished early sentence waits for held final acceptance');
 finalResponse.resolve(response({state:'done',seconds:2,speech_streamable:true,speech_epoch:1,result:{reply:'The answer is forty-two. More explanation.'}}));await finalPoll;await settle();
 assert.equal(audioContext.sources.length,2);assert.equal(posts(real.h).length,2);audioContext.sources[1].finish();await settle();
 assert.equal(posts(real.h).length,3);assert.equal(posts(real.h).at(-1).body.mode,'dictate');
 assert.equal(real.h.requests.filter(r=>r.url==='/api/speech').map(r=>r.body.text).join(''),'The answer is forty-two. More explanation.');
 console.log('Streaming conversation PASS: actual audio before held final result, exact content, final-only listening, job/epoch binding, retry, End, hidden/locked/disconnected tabs, errors, pending acceptance, other-tab isolation and final-only fallback.');
})().catch(error=>{console.error(error);process.exitCode=1;});
