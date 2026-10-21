'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {setup}=require('./test-workspace.cjs');
const {context,elements}=setup({runWorkspace:false});
context.requestAnimationFrame=()=>{throw new Error('The waveform must not start an animation loop');};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/folded-core.js'),'utf8'),context);
const wave=new context.window.PrometheusOrb.VoiceWaveform(elements.voiceWave,elements.voiceSource);
const heights=()=>elements.voiceWave.children.map(bar=>parseFloat(bar.style.height));
const flat=()=>assert(heights().every(height=>height===3),'a processing phase cannot retain a microphone or playback trace');
assert.equal(elements.voiceWave.children.length,32);
assert.equal(elements.voiceSource.textContent,'Microphone off');
assert.equal(wave.sample(1,0,'input'),false);flat();

wave.setState('listening');assert.equal(wave.sample(.8,100,'input'),true);
assert.equal(elements.voiceSource.textContent,'Microphone input');
assert(heights().at(-1)>32&&heights().at(-1)<33);
const measured=heights();wave.setState('listening');assert.deepEqual(heights(),measured,'repeated status polls preserve measured history');
assert.equal(wave.sample(1,200,'output'),false);assert.deepEqual(heights(),measured,'playback is never presented as microphone input');
for(const phase of ['opening','transcribing','thinking','preparing','idle','error','offline']){
 wave.setState(phase);flat();
 assert.equal(wave.sample(1,300,'input'),false);assert.equal(wave.sample(1,300,'output'),false);flat();
 assert.equal(elements.voiceWave.dataset.phase,phase);
}
wave.setState('speaking');assert.match(elements.voiceSource.textContent,/Voice output.*microphone off/);
assert.equal(wave.sample(1,1000,'input'),false);flat();
assert.equal(wave.sample(.5,1000,'output'),true);
const firstFrame=heights();assert.equal(wave.sample(.9,1049,'output'),false);
assert.deepEqual(heights(),firstFrame,'playback redraws are bounded to twenty per second');
assert.equal(wave.sample(.9,1050,'output'),true);assert(heights().at(-1)>36);
wave.setState('listening');flat();
assert.equal(wave.sample(2,0,'input'),true);assert.equal(heights().at(-1),40);
for(const value of [-1,NaN,Infinity]){wave.sample(value,0,'input');assert.equal(heights().at(-1),3);}
for(let index=0;index<1000;index++)wave.sample(0,index,'input');
flat();assert.equal(elements.voiceWave.children.length,32,'long conversations keep a fixed number of nodes');
wave.sample(.9,1001,'input');wave.reset();flat();
wave.setState('unknown');assert.equal(elements.voiceWave.dataset.phase,'idle');
assert.equal(wave.sample(1,2000,'input'),false);
console.log('Voice waveform passed: measured input/output isolation, explicit processing states, cleared phase transitions, stable history, bounded rendering, invalid levels and fixed memory.');
