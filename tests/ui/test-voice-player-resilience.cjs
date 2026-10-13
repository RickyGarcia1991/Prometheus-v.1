'use strict';
const assert=require('node:assert/strict');
const {LocalVoice,speechText}=require('../../src/prometheus_assistant/ui_assets/voice-player.js');
const audio={sampleRate:16000,duration:2,numberOfChannels:1,getChannelData:()=>new Float32Array(32000)};
let context;
global.AudioContext=class{
 constructor(){context=this;this.state='running';this.currentTime=0;this.destination={};this.listeners=new Set();}
 async resume(){if(this.rejectResume)throw Error('device unavailable');if(this.blockResume)return new Promise(()=>{});this.setState('running');}
 async suspend(){this.setState('suspended');}
 setState(state){this.state=state;for(const listener of [...this.listeners])listener();}
 addEventListener(type,listener){assert.equal(type,'statechange');this.listeners.add(listener);}
 removeEventListener(type,listener){assert.equal(type,'statechange');this.listeners.delete(listener);}
 async decodeAudioData(){if(this.suspendOnDecode)this.setState('suspended');return audio;}
 createBufferSource(){return {connect(){},disconnect(){},start(){},stop(){}};}
};
const reply={encoding:'wav-base64',audio:'AAAA'};
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
(async()=>{
 for(const text of ['😀'.repeat(4100),'a'.repeat(4100)])assert(Array.from(speechText(text)).length<=4000,'unbroken text respects the speech service limit');
 assert.equal(speechText('Plain answer: 6*7 = 42; snake_case stays.'),'Plain answer: 6*7 = 42; snake_case stays.');
 assert.equal(speechText('- 5\n+ 3'),'- 5 + 3','cleanup preserves signed numbers');
 assert.equal(speechText('Read [manual][docs].\n[docs]: https://example.com'),'Read manual.');
 assert.equal(speechText('~~~js\nunfinished();'),'A code example is shown in the conversation.');
 const player=new LocalVoice();
 const formatted='## The answer\n\n**Six times seven is 42.** Read [the guide](https://example.com/a_(b)).\n\n```python\nprint(6 * 7)\n```\nThe next answer is *50*. Use `snake_case`; 6*7 is unchanged.';
 let spoken;
 await player.play(formatted,async(_path,body)=>{spoken=body.text;return reply;},()=>{},()=>{});
 assert(spoken.includes('Six times seven is 42.')&&spoken.includes('The next answer is 50.'));
 assert(!spoken.includes('**')&&!spoken.includes('##')&&!spoken.includes('https://')&&!spoken.includes('print(6'));
 assert(spoken.includes('A code example is shown in the conversation.'));
 assert(spoken.includes('Read the guide.')&&spoken.includes('snake_case')&&spoken.includes('6*7'));
 assert.equal(formatted.startsWith('##'),true,'speech cleanup never changes the displayed source answer');
 player.stop();
 const long='The first sentence is complete. '+('One more sentence ends here. '.repeat(180));
 const spokenParts=[];
 await player.play(long,async(_path,body)=>{spokenParts.push(body.text);return reply;},()=>{},()=>{});
 for(let index=0;player.source&&index<30;index++){player.source.onended();await settle();}
 spoken=spokenParts.join('');
 assert(spoken.length<=4000&&spoken.endsWith('The rest of the answer is in the conversation.'));
 assert(/ends here\. The rest/.test(spoken),'long speech stops at a complete sentence and announces omitted text');
 player.stop();

 const afterDecode=new LocalVoice();await afterDecode.unlock();context.suspendOnDecode=true;
 await afterDecode.play('resume after decoding',async()=>reply,()=>{},()=>{});
 assert.equal(context.state,'running','context interruption during decoding is recovered before playback');
 const activeContext=context;let failures=0,normalEnds=0;
 afterDecode.stop();activeContext.suspendOnDecode=false;
 await afterDecode.play('recover during reply',async()=>reply,()=>{},()=>normalEnds++,()=>failures++);
 activeContext.setState('interrupted');await settle();
 assert.equal(activeContext.state,'running');assert(afterDecode.source,'successful recovery keeps the same reply playing');
 activeContext.rejectResume=true;activeContext.setState('suspended');await settle();
 assert.equal(failures,1,'unrecoverable interruption reports exactly one error');
 assert.equal(normalEnds,0,'failed playback must not trigger the next microphone turn');
 assert.equal(afterDecode.source,null);assert.equal(activeContext.listeners.size,0);
 activeContext.setState('closed');await settle();assert.equal(failures,1);

 const closing=new LocalVoice();await closing.play('device closed',async()=>reply,()=>{},()=>normalEnds++,()=>failures++);
 const deviceEnded=closing.source.onended;context.setState('closed');deviceEnded();await settle();
 assert.equal(failures,2,'device closure and its ended event produce one failure');assert.equal(normalEnds,0);

 const abandoned=new LocalVoice();await abandoned.unlock();const abandoning=context;
 await abandoned.play('end while recovering',async()=>reply,()=>{},()=>normalEnds++,()=>failures++);
 let releaseResume;abandoning.resume=()=>new Promise(r=>{releaseResume=r;});
 abandoning.setState('interrupted');abandoned.stop();abandoning.state='running';releaseResume();await settle();
 assert.equal(abandoned.source,null);assert.equal(failures,2);assert.equal(normalEnds,0);

 // Model a browser that leaves resume() pending after interruption, without hardware.
 const blocked=new LocalVoice();await blocked.unlock();context.blockResume=true;context.state='suspended';
 const originalSet=global.setTimeout,originalClear=global.clearTimeout;let timeout,delay,cleared=false;
 global.setTimeout=(fn,ms)=>{timeout=fn;delay=ms;return 99;};global.clearTimeout=id=>{if(id===99)cleared=true;};
 try{
  const operation=blocked.play('blocked audio',async()=>reply,()=>assert.fail('must not start'),()=>assert.fail('must not advance'));
  await settle();assert.equal(typeof timeout,'function');assert(delay>0&&delay<=5000);timeout();
  await assert.rejects(operation,/playback.*interrupted/i);assert(cleared);
 }finally{global.setTimeout=originalSet;global.clearTimeout=originalClear;blocked.stop();}
 console.log('Voice resilience PASS: speech cleanup, sentence limits, suspended-context recovery, bounded failure and stale cancellation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
