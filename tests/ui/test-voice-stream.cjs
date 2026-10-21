'use strict';
const assert=require('node:assert/strict');
const {LocalVoice,speechText,stableSpeechPrefix,streamChunk}=require('../../src/prometheus_assistant/ui_assets/voice-player.js');
const {settle,deferred}=require('./test-app.cjs');
let context;
global.AudioContext=class{
 constructor(){context=this;this.state='running';this.currentTime=0;this.destination={};this.listeners=new Set();this.sources=[];this.decoded=0;this.durations=[];}
 async resume(){this.state='running';}async suspend(){this.state='suspended';}
 addEventListener(_type,listener){this.listeners.add(listener);}removeEventListener(_type,listener){this.listeners.delete(listener);}
 async decodeAudioData(){this.decoded++;return {sampleRate:16000,duration:this.durations.shift()||1,numberOfChannels:1,getChannelData:()=>new Float32Array(1600)};}
 createBufferSource(){const owner=this,source={active:false,connect(){},disconnect(){},start(){assert(!owner.sources.some(other=>other.active),'chunks never overlap');this.active=true;},stop(){this.active=false;},finish(){this.active=false;this.onended?.();}};this.sources.push(source);return source;}
};
function harness(){
 const player=new LocalVoice(),requests=[],events=[];let active=0,maxActive=0;
 const stream=player.stream((path,body)=>{assert.equal(path,'/api/speech');const held=deferred();active++;maxActive=Math.max(maxActive,active);
  requests.push({text:body.text,resolve(){active--;held.resolve({encoding:'wav-base64',audio:'AAAA'});},reject(){active--;held.reject(Error('Synthetic synthesis failure'));}});return held.promise;
 },()=>events.push('started'),()=>events.push('finished'),error=>events.push(error.message));
 return {player,requests,events,stream,get maxActive(){return maxActive;}};
}
async function drain(h){
 for(let turn=0;turn<40&&!h.events.includes('finished');turn++){
  for(const request of h.requests)if(!request.resolved){request.resolved=true;request.resolve();}
  await settle();h.player.source?.finish();await settle();
 }
 assert(h.events.includes('finished'),'finite queue drains after final acceptance');
}
(async()=>{
 assert.equal(stableSpeechPrefix('Hello there. **unfinished'), 'Hello there. ');
 assert.equal(stableSpeechPrefix('[Unfinished link'), '');
 assert.equal(streamChunk('Dr. Garcia is here. Next',false,true),'Dr. Garcia is here.');
 assert.equal(streamChunk('Incomplete thought',false,true),'');
 assert.equal(streamChunk('A decimal value of 6.25 is useful. Next',false,true),'A decimal value of 6.25 is useful.');
 const early=harness();
 early.stream.update('The answer is forty-two. More details',false);await settle();
 assert.equal(early.requests.length,1);assert.equal(early.requests[0].text,'The answer is forty-two.');
 early.requests[0].resolved=true;early.requests[0].resolve();await settle();
 assert.deepEqual(early.events,['started'],'first audio starts while final answer is still unavailable');
 context.sources[0].finish();await settle();assert.deepEqual(early.events,['started'],'audio ending cannot finish a still-generating answer');
 early.stream.update('The answer is forty-two. More details are ready. And still generating',false);await settle();
 assert.equal(early.requests[1].text,' More details are ready.');
 const final='The answer is forty-two. More details are ready. And still generating ends here.';
 early.stream.update(final,true);await drain(early);
 assert.deepEqual(early.events,['started','finished']);assert.equal(early.requests.map(r=>r.text).join(''),speechText(final));
 assert.equal(early.maxActive,1);assert.equal(context.listeners.size,0);

 const queued=harness(),answer='First short sentence. '+Array.from({length:45},(_,i)=>`Distinct sentence ${i} is an additional detail.`).join(' ');
 queued.stream.update(answer+' ',false);await settle();queued.requests[0].resolved=true;queued.requests[0].resolve();await settle();
 assert.equal(queued.requests.length,2,'only one next chunk is prefetched while first plays');
 queued.requests[1].resolved=true;queued.requests[1].resolve();await settle();
 assert.equal(queued.requests.length,2,'a decoded next buffer does not allow a third request');
 queued.stream.update(answer,true);await drain(queued);
 assert.equal(queued.requests.map(r=>r.text).join(''),speechText(answer));
 assert(queued.requests.every(r=>Array.from(r.text).length<=700));assert.equal(queued.maxActive,1);

 for(const finalText of ['Plain first sentence. **Formatted** final sentence.', '😀 '.repeat(2300), 'x'.repeat(5000), 'Sentence. '+('long word '.repeat(600))]){
  const h=harness();h.stream.update(finalText+' ',false);await settle();h.stream.update(finalText,true);await drain(h);
  assert.equal(h.requests.map(r=>r.text).join(''),speechText(finalText),'final normalization preserves exactly the already spoken prefix');
  assert(Array.from(h.requests.map(r=>r.text).join('')).length<=4000);
  assert(h.requests.every(r=>!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(r.text)));
 }
 const markup=harness();markup.stream.update('**An unfinished',false);await settle();assert.equal(markup.requests.length,0);
 markup.stream.update('**An unfinished thought is now complete.**',true);await drain(markup);
 assert.equal(markup.requests[0].text,'An unfinished thought is now complete.');

 for(const changedFinal of [false,true]){
  const changed=harness();changed.stream.update('Original sentence. Next',false);await settle();changed.requests[0].resolve();await settle();
  const staleEnd=context.sources[0].onended;changed.stream.update('Revised sentence. Next',changedFinal);staleEnd();await settle();
  assert.equal(changed.player.source,null);assert.equal(changed.events.length,2);assert.match(changed.events[1],/streamed reply changed/);
  assert(!changed.events.includes('finished'));assert.equal(changed.stream.update('Original sentence. Next',true),false);
 }
 for(const cancelBeforeFirst of [false,true]){
  const cancelled=harness();cancelled.stream.update('First sentence. Second sentence. Third sentence. ',false);await settle();
  if(!cancelBeforeFirst){cancelled.requests[0].resolve();await settle();}
  const oldContext=context,decoded=oldContext.decoded,lateEnd=cancelled.player.source?.onended;
  cancelled.stream.cancel();cancelled.requests.at(-1).resolve();lateEnd?.();await settle();
  assert.equal(oldContext.decoded,decoded,'late cancelled TTS discarded before decode');assert.equal(cancelled.player.source,null);
  assert(!cancelled.events.includes('finished'));assert.equal(oldContext.listeners.size,0);
 }
 const replaced=harness();replaced.stream.update('Old generation. More',false);await settle();
 const replacement=replaced.player.stream(async()=>({encoding:'wav-base64',audio:'AAAA'}),()=>replaced.events.push('new started'),()=>replaced.events.push('new finished'));
 replacement.update('New accepted response.',true);await settle();const replacementSource=replaced.player.source;
 replaced.requests[0].resolve();await settle();assert.equal(replaced.player.source,replacementSource);replacementSource.finish();await settle();
 assert.deepEqual(replaced.events,['new started','new finished']);

 const failed=harness();failed.stream.update('First sentence. Second sentence. ',false);await settle();failed.requests[0].resolve();await settle();
 const failedEnd=context.sources[0].onended;failed.requests[1].reject();await settle();failedEnd();
 assert.deepEqual(failed.events,['started','Synthetic synthesis failure']);assert.equal(failed.player.source,null);
 const bounded=harness();await bounded.player.unlock();context.durations=[170,140];bounded.stream.update('First sentence. Second sentence. ',false);await settle();
 bounded.requests[0].resolve();await settle();bounded.requests[1].resolve();await settle();
 assert.match(bounded.events.at(-1),/exceeded its limit/);assert(!bounded.events.includes('finished'));assert.equal(context.sources.length,1);
 console.log('Streaming speech PASS: audio before final answer, canonical exact-once content, final-only completion, bounded queue, Unicode/markup, changed prefix, cancellation, replacement, errors and total duration.');
})().catch(error=>{console.error(error);process.exitCode=1;});
