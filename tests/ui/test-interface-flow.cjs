'use strict';
// User event regressions with deterministic local transport; no device or network access.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const {fixture, settle, deferred} = require('./test-app.cjs');
const posts = h => h.requests.filter(row => row.url === '/api/jobs');
function voice(){return {stops:0,stop(){this.stops++;},suspend(){},async unlock(){},level(){return 0;},async play(text,api,start,end,error){this.end=end;this.error=error;start();}};}
async function finish(h,result){const id=h.run('job');h.jobs.set(id,{state:'done',seconds:1,result});await h.run(`pollJob('${id}')`);await settle();}
async function speakingChat(){const audio=voice(),h=await fixture({voice:audio});await h.elements.spokenMode.click();await settle();await h.elements.dictate.click();await settle();await finish(h,{transcript:'Hello'});await finish(h,{reply:'Hello to you'});return {h,audio};}

test('selecting the active spoken mode preserves playback and its next listening turn',async()=>{
 const {h,audio}=await speakingChat(),before=audio.stops;
 await h.elements.spokenMode.click();await settle();
 assert.equal(audio.stops,before,'clicking a selected mode must not interrupt the current reply');
 audio.end();await settle();
 assert.equal(posts(h).length,3,'the completed spoken reply still starts the next recording');
 assert.equal(posts(h).at(-1).body.mode,'dictate');
});

test('Start voice chat unlocks playback within its explicit click even when spoken mode is selected',async()=>{
 const audio=voice(),h=await fixture({voice:audio});let unlocks=0;audio.unlock=async()=>{unlocks++;};
 await h.elements.spokenMode.click();await settle();assert.equal(unlocks,1);
 await h.elements.dictate.click();await settle();assert.equal(unlocks,2,'Start renews audio authorization before opening the microphone');
 assert.equal(posts(h).length,1);assert.equal(posts(h)[0].body.mode,'dictate');
});

test('switching to Text only while Start is unlocking cannot open the microphone later',async()=>{
 const audio=voice(),h=await fixture({voice:audio});await h.elements.spokenMode.click();await settle();
 const unlock=deferred();audio.unlock=()=>unlock.promise;
 const start=h.elements.dictate.click();await settle();await h.elements.textMode.click();await settle();
 unlock.resolve();await start;await settle();assert.equal(posts(h).length,0);assert.equal(h.elements.textMode.getAttribute('aria-pressed'),'true');
});

test('a late playback failure stops hands-free input and presents the actionable error',async()=>{
 const {h,audio}=await speakingChat();
 assert.equal(typeof audio.error,'function','playback interruptions must have an application handler');
 audio.error(new Error('Audio playback was interrupted. Start voice chat again.'));await settle();
 assert.equal(h.elements.dictate.textContent,'◉ Record');
 assert.equal(h.elements.voiceCapture.hidden,true);
 assert.match(h.elements.notice.textContent,/Audio playback was interrupted/);
 audio.end();await settle();assert.equal(posts(h).length,2,'failed playback never restarts the microphone');
});

test('voice phases remain accurate between capture, transcription and pending playback',async()=>{
 const audio=voice(),h=await fixture({voice:audio}),e=h.elements;
 await e.spokenMode.click();await settle();await e.dictate.click();await settle();const id=h.run('job');
 h.jobs.set(id,{state:'running',recording:false,stage:'Microphone off · transcribing locally',elapsed:8});await h.run(`pollJob('${id}')`);
 assert.equal(e.activity.textContent,'Transcribing…');await h.run('status()');assert.equal(e.activity.textContent,'Transcribing…','generic status cannot overwrite the current capture stage');
 audio.play=async(text,api,start,end)=>{audio.start=start;audio.end=end;};
 await finish(h,{transcript:'Hello'});await finish(h,{reply:'Hello to you'});
 assert.equal(e.voiceState.textContent,'Preparing voice · microphone off');assert.equal(e.activity.textContent,'Preparing voice…');
 await h.run('status()');assert.equal(e.activity.textContent,'Preparing voice…');
 audio.start();assert.equal(e.activity.textContent,'Speaking…');
 audio.end();await settle();assert.match(e.voiceState.textContent,/Opening microphone/);
});

test('IME Enter commits text without submitting or leaving the open workspace',async()=>{
 const h=await fixture(),e=h.elements;
 h.run(fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/workspace.js'),'utf8'));
 e.openWorkbench.click();e.prompt.value='正在输入';
 e.prompt.dispatch('keydown',{key:'Enter',isComposing:true});await settle();
 assert.equal(posts(h).length,0,'composition Enter does not send an unfinished phrase');
 assert.equal(e.workbench.hidden,false,'composition Enter leaves the active workspace visible');
 e.prompt.dispatch('keydown',{key:'Enter',keyCode:229});await settle();assert.equal(posts(h).length,0);
 e.prompt.dispatch('keydown',{key:'Enter',shiftKey:true});await settle();assert.equal(posts(h).length,0,'Shift+Enter remains a newline');
 e.prompt.dispatch('keydown',{key:'Enter'});await settle();assert.equal(posts(h).length,1,'ordinary Enter still sends');
});

test('Escape dismisses the owner panel and returns focus to its opener',async()=>{
 const h=await fixture(),e=h.elements;e.ownerPanelToggle.click();await settle();assert.equal(e.ownerPanel.hidden,false);
 for(const fn of h.documentEvents.keydown||[])fn({key:'Escape',preventDefault(){}});
 assert.equal(e.ownerPanel.hidden,true);assert.equal(e.ownerPanelToggle.getAttribute('aria-expanded'),'false');assert.equal(e.ownerPanelToggle.focused,true);
});

test('live answer growth follows the reader at the bottom and leaves earlier reading undisturbed',async()=>{
 const h=await fixture(),e=h.elements;e.messages.clientHeight=200;e.messages.scrollTop=0;
 Object.defineProperty(e.messages,'scrollHeight',{get(){return 40+e.messages.children.reduce((sum,row)=>sum+(row.querySelector('.text')?.textContent.length||0)*2,0);}});
 e.prompt.value='Explain';await h.run('submit()');await settle();const id=h.run('job');
 h.jobs.set(id,{state:'running',partial:'a'.repeat(200),cancellable:true});await h.run(`pollJob('${id}')`);
 assert.equal(e.messages.scrollTop,e.messages.scrollHeight-e.messages.clientHeight,'reader at the end follows newly streamed content');
 e.messages.scrollTop=17;
 h.jobs.set(id,{state:'running',partial:'a'.repeat(400),cancellable:true});await h.run(`pollJob('${id}')`);
 assert.equal(e.messages.scrollTop,17,'reader looking at earlier content keeps that position');
 const answer=e.messages.children.at(-1);await finish(h,{reply:'a'.repeat(400),label:'Finished helper'});
 assert.equal(e.messages.children.at(-1),answer,'completion updates the existing answer instead of replacing its scroll anchor');
 assert.equal(e.messages.scrollTop,17);assert.equal(answer.querySelector('.label').textContent,'Finished helper');
});
