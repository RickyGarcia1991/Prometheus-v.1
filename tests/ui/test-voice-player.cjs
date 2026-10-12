'use strict';
const assert=require('node:assert/strict');
const {LocalVoice,envelope}=require('../../src/prometheus_assistant/ui_assets/voice-player.js');
const pcm=new Float32Array(16000*2);for(let i=8000;i<16000;i++)pcm[i]=Math.sin(i*.12)*.4;
const audio={sampleRate:16000,duration:2,numberOfChannels:1,getChannelData:()=>pcm};
const env=envelope(audio);assert(env.slice(0,45).every(x=>x===0));assert(Math.max(...env)>.9);assert(env.slice(105).every(x=>x===0));
let starts=0,stops=0,context;
global.AudioContext=class{
 constructor(){context=this;this.state='running';this.currentTime=0;this.outputLatency=.1;this.destination={};}
 async resume(){this.state='running';} async suspend(){this.state='suspended';}
 async decodeAudioData(){return audio;}
 createBufferSource(){return {connect(){},disconnect(){},start(){starts++;},stop(){stops++;}};}
};
(async()=>{
 const player=new LocalVoice();let resolve,ended=0,began=0;
 const pending=player.play('old',()=>new Promise(r=>{resolve=r;}),()=>began++,()=>ended++);
 await Promise.resolve();await Promise.resolve();player.stop();resolve({encoding:'wav-base64',audio:'AAAA'});await pending;
 assert.equal(starts,0,'stopped pending request must never play later');
 let captured;const unicode='a'.repeat(3999)+'😀'+'extra';
 await player.play(unicode,async(_path,body)=>{captured=body.text;return {encoding:'wav-base64',audio:'AAAA'};},()=>began++,()=>ended++);
 assert.equal(Array.from(captured).length,4000);assert(captured.endsWith('😀'),'truncation preserves complete Unicode codepoints');
 assert.equal(began,1);assert.equal(starts,1);
 context.currentTime=.9;assert(player.level(900)>.1,'heard voiced section pulses');
 context.currentTime=1.7;for(let i=0;i<20;i++)player.level(1700+i*20);assert(player.value<.003,'silence rests');
 player.suspend();assert.equal(stops,1);assert.equal(player.samples,null);assert.equal(player.level(2200),0);assert.equal(context.state,'suspended');
 console.log('Voice player PASS: measured PCM, latency, silence, stale reply cancellation, stop and suspend.');
})().catch(e=>{console.error(e);process.exitCode=1;});
