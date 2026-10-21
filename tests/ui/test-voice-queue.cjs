'use strict';
const assert=require('node:assert/strict');
const {LocalVoice,speechText,speechChunks}=require('../../src/prometheus_assistant/ui_assets/voice-player.js');
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const answer='This is the first complete sentence. '+Array.from({length:20},(_,index)=>`Later sentence ${index} adds a distinct useful detail to this accepted answer.`).join(' ');
let context;
global.AudioContext=class{
 constructor(){context=this;this.state='running';this.currentTime=0;this.destination={};this.listeners=new Set();this.sources=[];this.decoded=0;this.durations=[];}
 async resume(){this.state='running';}async suspend(){this.state='suspended';}
 addEventListener(_type,listener){this.listeners.add(listener);}removeEventListener(_type,listener){this.listeners.delete(listener);}
 async decodeAudioData(bytes){this.decoded++;return {id:new Uint8Array(bytes)[0],sampleRate:16000,duration:this.durations.shift()||1,numberOfChannels:1,getChannelData:()=>new Float32Array(1600)};}
 createBufferSource(){const source={active:false,connect(){},disconnect(){},start(){assert(!context.sources.some(other=>other.active),'two audio chunks cannot overlap');this.active=true;},stop(){this.active=false;},finish(){this.active=false;this.onended?.();}};this.sources.push(source);return source;}
};
const audio=index=>({encoding:'wav-base64',audio:Buffer.from([index]).toString('base64')});
function harness(){
 const player=new LocalVoice(),requests=[],events=[];let active=0,maxActive=0;
 const request=(path,body)=>{assert.equal(path,'/api/speech');const response=deferred();const index=requests.length;active++;maxActive=Math.max(maxActive,active);requests.push({text:body.text,resolve:()=>{active--;response.resolve(audio(index));},reject:()=>{active--;response.reject(Error('Synthetic synthesis failure'));}});return response.promise;};
 return {player,requests,events,request,get maxActive(){return maxActive;},play:()=>player.play(answer,request,()=>events.push('started'),()=>events.push('listening'),error=>events.push(error.message))};
}
(async()=>{
 for(const input of [answer,'😀'.repeat(4100),'x'.repeat(4100),'Dr. Garcia explains the setting. '+answer]){
  const parts=speechChunks(input);
  assert.equal(parts.join(''),speechText(input),'speech content is neither repeated nor omitted by chunking');
  assert(parts.every(part=>part.trim()&&Array.from(part).length<=700));
  assert(Array.from(parts[0]).length<=240);
  assert(parts.every(part=>!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(part)),'each chunk preserves surrogate pairs');
 }
 assert.equal(speechChunks('Dr. Garcia explains the setting. '+answer)[0],'Dr. Garcia explains the setting.','common titles do not split a first sentence');
 const normal=harness(),operation=normal.play();await settle();
 assert.equal(normal.requests.length,1);normal.requests[0].resolve();assert.equal(await operation,true);await settle();
 assert.equal(context.sources.length,1);assert(context.sources[0].active);
 assert.equal(normal.requests[0].text,'This is the first complete sentence.');
 assert.equal(normal.requests.length,2,'only one next TTS request is prefetched');
 assert.deepEqual(normal.events,['started'],'first audio starts before the held next TTS response resolves');
 context.sources[0].finish();await settle();
 assert.deepEqual(normal.events,['started'],'a gap awaiting next audio cannot reopen the microphone');
 assert.equal(context.sources.length,1);normal.requests[1].resolve();await settle();
 assert.equal(context.sources.length,2);assert(context.sources[1].active);assert.equal(normal.requests.length,3);
 for(let index=1;index<30&&!normal.events.includes('listening');index++){
  const next=normal.requests[index+1];if(next)next.resolve();await settle();
  context.sources[index].finish();await settle();
 }
 assert.deepEqual(normal.events,['started','listening'],'exactly one final completion enables listening');
 assert.equal(normal.requests.map(row=>row.text).join(''),answer);
 assert.equal(context.sources.length,normal.requests.length);assert.equal(normal.maxActive,1);
 assert.equal(normal.player.source,null);assert.equal(context.listeners.size,0);

 for(const endCurrentFirst of [false,true]){
  const cancelled=harness(),pending=cancelled.play();await settle();cancelled.requests[0].resolve();await pending;await settle();
  const originalContext=context,lateEnd=context.sources[0].onended;
  if(endCurrentFirst)context.sources[0].finish();
  cancelled.player.stop();cancelled.requests[1].resolve();lateEnd();await settle();
  assert.equal(originalContext.sources.length,1);assert.equal(originalContext.decoded,1,'cancelled prefetch response is discarded before decode');
  assert.deepEqual(cancelled.events,['started'],'End during playback or a synthesis gap cannot revive listening');
  assert.equal(cancelled.requests.length,2);assert.equal(originalContext.listeners.size,0);
 }

 const replaced=harness(),old=replaced.play();await settle();replaced.requests[0].resolve();await old;await settle();
 const oldSource=context.sources[0],oldEnd=oldSource.onended;
 await replaced.player.play('New accepted answer.',async()=>audio(8),()=>replaced.events.push('new started'),()=>replaced.events.push('new listening'));
 const newSource=context.sources.at(-1);replaced.requests[1].resolve();oldEnd();await settle();
 assert.equal(replaced.player.source,newSource);assert(newSource.active);newSource.finish();
 assert.deepEqual(replaced.events,['started','new started','new listening'],'a late old response cannot preempt or finish a replacement');

 const failed=harness(),failureStart=failed.play();await settle();failed.requests[0].resolve();await failureStart;await settle();
 const endedBeforeFailure=context.sources[0].onended;failed.requests[1].reject();await settle();endedBeforeFailure();await settle();
 assert.deepEqual(failed.events,['started','Synthetic synthesis failure']);assert.equal(failed.player.source,null);assert.equal(context.listeners.size,0);
 assert.equal(failed.requests.length,2,'a failed prefetch neither skips text nor advances the queue');

 const overLimit=harness();await overLimit.player.unlock();context.durations=[170,140];const limited=overLimit.play();await settle();
 overLimit.requests[0].resolve();await limited;await settle();overLimit.requests[1].resolve();await settle();
 assert.equal(overLimit.events.length,2);assert.match(overLimit.events[1],/exceeded its limit/);
 assert.equal(context.sources.length,1);assert.equal(overLimit.player.source,null);
 assert(!overLimit.events.includes('listening'),'the five-minute limit applies to all chunks together');
 console.log('Voice queue PASS: early first audio, one prefetch, exact content, sequential playback, final-only listening, cancellation, replacement, failure and cumulative limit.');
})().catch(error=>{console.error(error);process.exitCode=1;});
