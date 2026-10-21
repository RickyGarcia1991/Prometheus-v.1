'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
const {fixture,settle}=require('./test-app.cjs');
const assets=path.join(__dirname,'../../src/prometheus_assistant/ui_assets');
let paths=[],current=[];const calls=[];
const drawing={clearRect(){paths=[];},createLinearGradient(){return {addColorStop(){}};},beginPath(){current=[];},moveTo(x,y){current.push([x,y]);},lineTo(x,y){current.push([x,y]);},stroke(){paths.push(current);}};
const canvas={style:{},dataset:{},getBoundingClientRect:()=>({width:1800,height:800}),getContext(kind){calls.push(kind);assert.equal(kind,'2d');return drawing;}};
const browser={devicePixelRatio:4,Image:class{constructor(){throw Error('No image/texture may be loaded');}},requestAnimationFrame(){throw Error('Renderer must not own a loop');},fetch(){throw Error('Renderer must not fetch resources');}};
vm.runInNewContext(fs.readFileSync(path.join(assets,'folded-core.js'),'utf8'),{window:browser});
const ribbon=new browser.PrometheusOrb.FoldedCore(canvas);
assert.deepEqual(calls,['2d']);assert.equal(canvas.width,960);assert.equal(canvas.height,260);
assert.equal(paths.length,3);assert(paths.every(points=>points.length===97));
assert(paths.every(points=>points.every(point=>point[1]===points[0][1])),'idle consists only of flat lines');
const idle=JSON.stringify(paths);ribbon.draw(90000,'idle',1);assert.equal(JSON.stringify(paths),idle,'time or a stray amplitude cannot create idle motion');
ribbon.draw(1000,'thinking',1);assert.equal(JSON.stringify(paths),idle,'processing cannot impersonate an audio signal');
ribbon.draw(1000,'listening',.7);assert(paths.some(points=>points.some(point=>Math.abs(point[1]-points[0][1])>5)));
assert.equal(canvas.dataset.speechLevel,'0.7000');
ribbon.draw(1000,'speaking',0);assert.equal(JSON.stringify(paths),idle,'silent playback stays flat');
ribbon.draw(1000,'speaking',Infinity);assert.equal(JSON.stringify(paths),idle);
ribbon.configure(0);ribbon.draw(1000,'speaking',1);assert.equal(JSON.stringify(paths),idle,'still/reduced motion mode stays flat');
canvas.getBoundingClientRect=()=>({width:200,height:100});ribbon.draw(0,'idle',0);assert.equal(canvas.width,300);assert.equal(canvas.height,150,'DPR is capped at 1.5');

(async()=>{
 const app=await fixture(),e=app.elements;
 assert(e.conversationPanel.hidden,'waveform is the default view');
 assert.equal(app.run('raf'),null);assert.equal(app.run('paintTimer'),null,'idle starts no continuous animation');
 e.prompt.value='Keep this draft and view';await app.run("submit('chat')");await settle();
 assert(e.conversationPanel.hidden,'sending a question does not switch to the conversation');
 const id=app.run('job');app.jobs.set(id,{state:'done',seconds:1,result:{reply:'A completed answer',label:'Fixture'}});await app.run(`pollJob('${id}')`);await settle();
 assert(e.conversationPanel.hidden,'a completed answer does not force the view open');
 await e.conversationToggle.click();assert(!e.conversationPanel.hidden);assert.equal(e.conversationToggle.textContent,'Waveform');assert.equal(e.conversationToggle.getAttribute('aria-expanded'),'true');
 assert(e.messages.children.some(node=>node.querySelector('.text')?.textContent==='A completed answer'));
 await e.conversationToggle.click();assert(e.conversationPanel.hidden);assert.equal(e.conversationToggle.textContent,'Conversation');
 app.run('display.fps=30; microphoneOn=true; motion()');assert.notEqual(app.run('raf'),null);
 app.run('render(1000)');assert.equal(app.timers.at(-1).delay,50,'active renderer schedules no more than twenty frames per second');
 const timer=app.timers.at(-1);await e.conversationToggle.click();assert(timer.cancelled);assert.equal(app.run('raf'),null);assert.equal(app.run('paintTimer'),null);
 timer.fn();assert.equal(app.run('raf'),null,'a late timer cannot restart a hidden waveform');
 await e.conversationToggle.click();assert.notEqual(app.run('raf'),null);
 app.context.document.hidden=true;app.run('motion()');assert.equal(app.run('raf'),null);assert.equal(app.run('paintTimer'),null);
 app.context.document.hidden=false;e.workbench.hidden=false;app.run('motion()');assert.equal(app.run('raf'),null,'workbench pauses the large waveform');
 e.workbench.hidden=true;app.run('microphoneOn=false; speaking=false; motion()');assert.equal(app.run('raf'),null);

 const capture=await fixture();await capture.elements.dictate.click();await settle();const captureId=capture.run('dictationJob');
 capture.jobs.set(captureId,{state:'running',recording:true,level:.6,elapsed:3,cancellable:true,stage:'Listening'});await capture.run(`pollJob('${captureId}')`);await settle();
 for(const show of [true,false]){
  capture.run(`setConversationView(${show})`);
  assert.equal(capture.elements.voiceCapture.hidden,false);assert.equal(capture.elements.finishRecording.hidden,false);assert.equal(capture.elements.dictate.disabled,false);
 }
 assert.equal(capture.run('inputLevel'),.6,'main ribbons receive the measured native input level');
 const css=fs.readFileSync(path.join(assets,'style.css'),'utf8');
 assert(!/folded-core\.webp|approved-orb\.png/.test(css));
 assert(!fs.existsSync(path.join(assets,'folded-core.webp'))&&!fs.existsSync(path.join(assets,'approved-orb.png')));
 console.log('Ribbon view PASS: 2D only, no texture requests, bounded size/DPR/geometry, measured signal, still idle, 20fps active, explicit view preservation and visible capture controls.');
})().catch(error=>{console.error(error);process.exitCode=1;});
