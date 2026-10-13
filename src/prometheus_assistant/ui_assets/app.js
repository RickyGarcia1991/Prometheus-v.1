'use strict';
const $=id=>document.getElementById(id);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const policy=new PrometheusAdaptive.AdaptiveDisplay();
let token='',session=null,job=null,localJob=null,busy=false,closed=false,online=false,lastReply='',speaking=false,partialBox=null,lastSubmitted='',submitting=false,cancelling=false;
let historyEpoch=0,historyPending=false;
const transport=new PrometheusTransport.LocalClient();
transport.onRestart=()=>{stopVoiceChat(false);invalidateHistory();$('homeKey').value='';$('questionImage').value='';job=null;localJob=null;cancelling=false;if(partialBox)partialBox.querySelector('.label').textContent='Interrupted by restart · not saved';partialBox=null;if(!$('prompt').value)$('prompt').value=lastSubmitted;notice('Prometheus restarted. Saved conversations are retained; an unfinished request was not resubmitted.');refreshHistory();};
let hardware={},display={level:1,name:'Simple',fps:15,size:0},raf=null,lastPaint=0,paintCost=0,lag=0,frames=0,slowFrames=0,drawTotal=0,lastFrame=0;
let conversationView=false,inputLevel=0,inputLevelAt=-Infinity,paintTimer=null;
let preference='auto';try{preference=localStorage.getItem('prometheus.display')||'auto';session=null;}catch{}
if(!['auto','simple','still'].includes(preference))preference='auto';$('quality').value=preference;
const canvas=$('fluid');
const orbRenderer=window.PrometheusOrb?new window.PrometheusOrb.FoldedCore(canvas):null;
const localVoice=window.PrometheusVoice?new window.PrometheusVoice.LocalVoice():null;
let spokenMode=false,speechEpoch=0;
let voiceChat=false,dictationJob=null,dictationPending=false,dictationEpoch=0,microphoneOn=false,finishingDictation=false;
let lastVoicePulse=0,voicePulsePending=false,voiceFrame=null,voiceEndNotice='',voiceActivity=null;
const discardedDictations=new Set();
const voiceWaveform=window.PrometheusOrb?.VoiceWaveform?new window.PrometheusOrb.VoiceWaveform($('voiceWave'),$('voiceSource')):null;
function resetWave(){voiceWaveform?.reset();}
function showLevel(level,source='input',now=performance.now()){voiceWaveform?.sample(level,now,source);if(source==='input'){inputLevel=Number.isFinite(level)?Math.max(0,Math.min(1,level)):0;inputLevelAt=now;motion();}}
function voiceControls(){
 const engaged=voiceChat||dictationJob!==null||dictationPending;
 $('voiceCapture').hidden=!engaged;
 $('dictate').textContent=voiceChat?'End voice chat':engaged?'Cancel recording':spokenMode?'Start voice chat':'◉ Record';
 $('dictate').setAttribute('aria-pressed',String(engaged));
 $('dictate').disabled=!engaged&&(busy||closed||!online||historyPending);
 $('finishRecording').hidden=!microphoneOn;
 $('finishRecording').disabled=finishingDictation;
}
function voiceState(text,state=null){voiceActivity=state;if(state)activity(state);$('voiceState').textContent=text;voiceControls();}
function stopVoiceChat(cancel=true){
 if(voiceChat){spokenMode=false;$('textMode').setAttribute('aria-pressed','true');$('spokenMode').setAttribute('aria-pressed','false');}
 const captured=dictationJob;if(captured)discardedDictations.add(captured);dictationEpoch++;voiceChat=false;dictationJob=null;dictationPending=false;microphoneOn=false;finishingDictation=false;voiceActivity=null;
 if(voiceFrame!==null){cancelAnimationFrame(voiceFrame);voiceFrame=null;}
 resetWave();$('voiceElapsed').textContent='';voiceControls();
 if(cancel&&captured)api('/api/cancel',{id:captured}).catch(()=>{});
}
async function beginRecording(){
 if(closed||!online||busy||document.hidden)return false;
 voiceEndNotice='';
 dictationPending=true;microphoneOn=false;finishingDictation=false;lastVoicePulse=0;
 resetWave();$('voiceElapsed').textContent='';voiceState('Opening microphone…','opening');
 const accepted=await submit('dictate');
 if(!accepted){stopVoiceChat();return false;}return true;
}
function listenAfterReply(){
 if(!voiceChat||closed||!online||document.hidden)return;
 if(busy){
  voiceState('Waiting for local work…','thinking');const epoch=dictationEpoch;
  setTimeout(()=>{if(epoch===dictationEpoch)listenAfterReply();},500);return;
 }
 beginRecording();
}
async function toggleRecording(){
 if(voiceChat||dictationJob||dictationPending){voiceEndNotice=voiceChat?'Voice chat ended. Microphone off.':'Recording cancelled. Microphone off.';stopVoiceChat();stopSpeaking();notice(voiceEndNotice);return;}
 if(busy||closed||!online||historyPending)return;
 if(spokenMode){
  if($('prompt').value.trim()){notice('Send or clear your draft before starting voice chat.');$('prompt').focus();return;}
  // Renew playback authorization in this click even if the mode is already selected.
  const epoch=speechEpoch;
  try{await localVoice.unlock();}catch(error){if(epoch===speechEpoch)notice(error.message);return;}
  if(epoch!==speechEpoch||!spokenMode||document.hidden)return;
  voiceChat=true;
 }
 await beginRecording();
}
async function finishRecording(){
 if(!dictationJob||!microphoneOn||finishingDictation)return;
 finishingDictation=true;voiceControls();
 try{await api('/api/dictation/finish',{id:dictationJob});voiceState('Finishing recording…','finishing');}
 catch(error){finishingDictation=false;voiceControls();notice(error.message);}
}
function trackRecording(id,result){
 if(dictationJob!==id)return;
 microphoneOn=result.recording===true;
 const transcribing=result.stage?.startsWith('Microphone off');
 voiceState(microphoneOn?(finishingDictation?'Finishing recording…':'Listening · take your time, pause to send'):transcribing?'Microphone off · transcribing locally':'Opening microphone…',microphoneOn?(finishingDictation?'finishing':'listening'):transcribing?'transcribing':'opening');
 const elapsed=Math.max(0,Number(result.elapsed)||0);
 $('voiceElapsed').textContent=Math.floor(elapsed/60)+':'+String(Math.floor(elapsed%60)).padStart(2,'0');
 showLevel(microphoneOn?result.level:0);
 if(microphoneOn&&!document.hidden&&!voicePulsePending&&performance.now()-lastVoicePulse>=1500){
  lastVoicePulse=performance.now();voicePulsePending=true;
  api('/api/dictation/pulse',{id}).catch(error=>{if(dictationJob===id&&![400,409].includes(error.status)){stopVoiceChat();notice('Voice input stopped after a connection problem.');}}).finally(()=>{voicePulsePending=false;});
 }
}
function playbackWave(now){
 voiceFrame=null;if(!voiceChat||!speaking||document.hidden)return;
 showLevel(localVoice?.level(now)||0,'output',now);voiceFrame=requestAnimationFrame(playbackWave);
}
function growPrompt(){const prompt=$('prompt');prompt.style.height='auto';prompt.style.height=Math.min(160,Math.max(48,prompt.scrollHeight||48))+'px';}

function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function rememberSession(value){session=value;try{if(value)localStorage.setItem('prometheus.session',value);else localStorage.removeItem('prometheus.session');}catch{}}
function invalidateHistory(){historyEpoch++;historyPending=false;$('history').value=session||'';}
function activity(state){voiceWaveform?.setState(state);$('orb').dataset.state=['opening','finishing','transcribing','preparing'].includes(state)?'thinking':state;const labels={idle:['I’m here.','What shall we work on?'],thinking:['Thinking…','Working on your request'],opening:['Opening microphone…','Starting local recording'],finishing:['Finishing recording…','Closing the microphone'],transcribing:['Transcribing…','Microphone off · reading your words locally'],preparing:['Preparing voice…','Microphone off · preparing the spoken reply'],listening:['Listening…','Pause to finish, or press Stop'],speaking:['Speaking…','Reading your answer'],error:['Let’s adjust.','See the message below'],offline:['Disconnected','Your text stays here']};const pair=labels[state]||labels.idle;$('activity').textContent=pair[0];$('activityNote').textContent=pair[1];motion();}
async function api(path,data){try{return await transport.request(path,data);}catch(error){if(error.status===401){stopVoiceChat(false);window.dispatchEvent(new CustomEvent('prometheus-auth-lost'));invalidateHistory();stopSpeaking();$('messages').replaceChildren();location.replace('/');}throw error;}}
function renderSourceLinks(content,text){
 // Render the stored source footer without interpreting answer text as HTML.
 // This also works for older saved conversations, which contain only text.
 const lines=String(text).split('\n');
 const parts=lines.map(line=>{
  const named=line.match(/^(\[S\d+\]\s+)(.+?) — (https?:\/\/\S+)(.*)$/);
  const bare=line.match(/^(https?:\/\/\S+)(\s+\|.*)?$/);
  if(!named&&!bare)return {text:line};
  try{
   const url=new URL(named?named[3]:bare[1]);
   if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return {text:line};
   return {prefix:named?named[1]:'',label:named?named[2]:url.hostname,url:url.href,suffix:named?named[4]:(bare[2]||'')};
  }catch{return {text:line};}
 });
 if(!parts.some(part=>part.url)){content.textContent=text;return;}
 const literal=value=>{if(value){const span=document.createElement('span');span.textContent=value;content.append(span);}};
 parts.forEach((part,index)=>{
  if(index)literal('\n');
  if(!part.url){literal(part.text);return;}
  literal(part.prefix);
  const link=document.createElement('a');link.className='source-link';link.textContent=part.label;
  link.href=part.url;link.title=part.url;link.target='_blank';link.rel='noopener noreferrer';
  content.append(link);literal(part.suffix);
 });
}
function fillMessage(box,role,text,label='',details=null){
 box.className='message '+role;
 const tag=document.createElement('div');tag.className='label';tag.textContent=label||(role==='user'?'You':'Prometheus');
 const content=document.createElement('div');content.className='text';
 if(role==='assistant')renderSourceLinks(content,text);else content.textContent=text;
 box.replaceChildren(tag,content);
 if(details){const panel=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Sources and result details';pre.textContent=JSON.stringify(details,null,2);panel.append(summary,pre);box.append(panel);}
}
function followingConversation(){const messages=$('messages');return messages.scrollHeight-messages.clientHeight-(messages.scrollTop||0)<80;}
function append(role,text,label='',details=null){
 $('welcome')?.remove();const box=document.createElement('article');fillMessage(box,role,text,label,details);
 const messages=$('messages'),follow=role==='user'||role==='error'||followingConversation()||!messages.children.length;
 messages.append(box);while(messages.children.length>80)messages.firstChild.remove();
 if(follow)messages.scrollTop=Math.max(0,(box.offsetTop||0)-8);
 $('latestReply').hidden=false;return box;
}
function updateAnswer(box,text,label,details=null){
 const messages=$('messages'),follow=followingConversation(),position=messages.scrollTop||0;
 fillMessage(box,'assistant',text,label,details);
 messages.scrollTop=follow?Math.max(0,messages.scrollHeight-messages.clientHeight):position;
}
$('latestReply').onclick=()=>{const messages=$('messages'),last=messages.children[messages.children.length-1];if(last)messages.scrollTop=Math.max(0,(last.offsetTop||0)-8);};
function setBusy(value){busy=value;for(const id of ['send','dictate','newChat','history','codingTools','models','capabilities','homeKey','questionImage'])$(id).disabled=value||closed||!online||(historyPending&&!['newChat','history'].includes(id));window.dispatchEvent(new CustomEvent('prometheus-busy',{detail:{busy,closed,online}}));if(!busy&&$('cancelRequest'))$('cancelRequest').hidden=true;voiceControls();}
function updateFields(){const mode=$('mode').value;const localCatalogue=mode==='reach'&&$('reachAction').value==='agent-resources';$('prompt').required=!localCatalogue;$('prompt').maxLength=localCatalogue?240:4000;$('reachHelp').textContent=localCatalogue?'Search the local project and capability catalogue (up to 240 characters), or leave blank to browse. No web access is required. Listed projects are references; this does not install or run them.':'Web search sends your query to Firecrawl and saves source links and snippets locally. Limited keyless access is ready to use. For other operations, enter a public URL or repository. Pages requiring sign-in or JavaScript may be unavailable.';$('reachField').hidden=mode!=='reach';$('homeKeyField').hidden=mode!=='home';$('sourceField').hidden=mode!=='ask';$('imageField').hidden=mode!=='vision';$('profileField').hidden=mode!=='library'&&!(mode==='ask'&&$('evidenceSource').value==='offline');$('providerField').hidden=mode!=='research';$('languageField').hidden=mode!=='coding';$('projectField').hidden=mode!=='project'&&!(mode==='ask'&&$('evidenceSource').value==='project');$('prompt').placeholder=mode==='reach'?({'agent-resources':'Project, capability, or all','web-search':'Search the web for a topic…',web:'https://example.com/article',feed:'https://example.com/feed.xml','github-repo':'Panniantong/Agent-Reach','github-releases':'Panniantong/Agent-Reach','github-search':'portable local AI',youtube:'https://www.youtube.com/watch?v=…','youtube-transcript':'https://www.youtube.com/watch?v=…'}[$('reachAction').value]):mode==='ask'?'Ask a question about your selected sources…':mode==='vision'?'What would you like to know about this image?':mode==='home'?'For example: turn off bedroom lights; status front porch lights':mode==='calculate'?'For example: (24 * 17) / 3':mode==='project'?'Search for a function, class, or phrase…':mode==='coding'?'Describe what you want to build or fix…':'Speak your mind…';}
function adapt(){const previous=display.level;display=policy.update({free:hardware.available_ram_gib,threads:hardware.cpu_threads||2,busy,cost:paintCost,lag,hidden:document.hidden,reduced:reduced.matches,preference},performance.now());if(previous!==display.level){paintCost=lag=frames=slowFrames=drawTotal=0;orbRenderer?.configure(display.level);}else if(orbRenderer)orbRenderer.level=display.level;$('tier').textContent=display.name;$('qualityStatus').textContent=display.name+' · '+display.reason;motion();}
function visualActive(){return !closed&&!document.hidden&&!conversationView&&$('workbench').hidden&&display.fps>0&&(speaking||microphoneOn);}
function render(now){
 raf=null;if(!visualActive())return;
 const state=speaking?'speaking':finishingDictation?'finishing':'listening';
 const measured=speaking?(localVoice?.level(now)||0):(now-inputLevelAt<900?inputLevel:0);
 orbRenderer?.draw(now,state,measured);
 paintTimer=setTimeout(()=>{paintTimer=null;if(visualActive())raf=requestAnimationFrame(render);},Math.max(50,1000/Math.min(20,display.fps)));
}
function motion(){
 if(!visualActive()){
  if(raf!==null){cancelAnimationFrame(raf);raf=null;}
  if(paintTimer!==null){clearTimeout(paintTimer);paintTimer=null;}
  orbRenderer?.pause();if(!document.hidden&&!conversationView&&$('workbench').hidden)orbRenderer?.draw(0,$('orb').dataset.state||'idle',0);return;
 }
 if(raf===null&&paintTimer===null)raf=requestAnimationFrame(render);
}
function setConversationView(show){
 conversationView=!!show;$('conversationPanel').hidden=!conversationView;$('mainStage').dataset.view=conversationView?'conversation':'waveform';
 $('conversationToggle').textContent=conversationView?'Waveform':'Conversation';$('conversationToggle').setAttribute('aria-expanded',String(conversationView));
 window.dispatchEvent(new CustomEvent('prometheus-show-conversation'));motion();
}
$('conversationToggle').onclick=()=>setConversationView(!conversationView);
new MutationObserver(motion).observe($('workbench'),{attributes:true,attributeFilter:['hidden']});
window.addEventListener('resize',()=>{if(!visualActive())motion();});
async function refreshHistory(){try{const rows=await api('/api/sessions');const selected=historyPending?$('history').value:session;$('history').replaceChildren(new Option('Choose a conversation…',''));for(const row of rows){const when=new Date(row.created_at).toLocaleString();$('history').add(new Option(`${when} · ${row.turn_count} turns`,row.session_id));}if(selected)$('history').value=selected;}catch(error){notice(error.message);}}
async function pollJob(id){
 if(job!==id||closed)return;
 try{const result=await api('/api/job/'+id);if(job!==id)return;
 if(result.state!=='running'&&discardedDictations.delete(id)){
  if(localJob===id)localJob=null;job=null;cancelling=false;partialBox=null;setBusy(false);activity('idle');notice(voiceEndNotice||'Microphone off. Recording discarded.');adapt();return;
 }
 if(result.background===true){
  if(result.state==='running'){setTimeout(()=>pollJob(id),document.hidden?5000:350);return;}
  if(localJob===id)localJob=null;job=null;cancelling=false;setBusy(false);activity('idle');adapt();return;
 }
 if(result.state==='running'){trackRecording(id,result);$('activityNote').textContent=result.stage||'Working on your request';if($('cancelRequest')){$('cancelRequest').hidden=!result.cancellable||dictationJob===id;$('cancelRequest').disabled=cancelling;}if(result.partial){if(!partialBox)partialBox=append('assistant',result.partial,'In progress · not saved');else updateAnswer(partialBox,result.partial,'In progress · not saved');window.dispatchEvent(new CustomEvent('prometheus-partial',{detail:{text:result.partial}}));}setTimeout(()=>pollJob(id),document.hidden?5000:350);return;}
 const ownDictation=dictationJob===id,continueVoice=ownDictation&&voiceChat;const origin=localJob===id?'local':'observed';if(ownDictation){dictationJob=null;microphoneOn=false;finishingDictation=false;voiceActivity=null;voiceControls();}if(localJob===id)localJob=null;job=null;cancelling=false;const completedPartial=partialBox;partialBox=null;$('requestTime').textContent=result.seconds+' s';
 if(result.state==='cancelled'&&result.action==='dictate'&&result.reason==='empty_recording'){stopVoiceChat(false);stopSpeaking();if(completedPartial)completedPartial.remove();activity('idle');voiceEndNotice=result.error;notice(voiceEndNotice);}
 else if(result.state==='error'||result.state==='cancelled'){stopVoiceChat(false);if(completedPartial)completedPartial.querySelector('.label').textContent='Incomplete · not saved';append('error',result.error,result.state==='cancelled'?'Request stopped':'Request needs attention');activity('error');if(!$('prompt').value)$('prompt').value=lastSubmitted;window.dispatchEvent(new CustomEvent('prometheus-result',{detail:{action:result.action,error:result.error,origin,job_id:id}}));notice('You can edit your request and try again.');}
 else{const r=result.result;if(r.transcript){if(completedPartial)completedPartial.remove();if(ownDictation){const draft=$('prompt').value.trim();if(draft&&voiceChat)stopVoiceChat(false);$('prompt').value=draft?draft+'\n'+r.transcript:r.transcript;growPrompt();notice('Dictation is ready. Review it, then send when you’re ready.');}}else{lastReply=r.reply||'';if(completedPartial)updateAnswer(completedPartial,lastReply,r.label,r.details);else append('assistant',lastReply,r.label,r.details);if(r.session)rememberSession(r.session);$('workLabel').textContent=r.label||'Request completed';$('saveStatus').textContent=r.session?'Saved locally':'Completed locally';window.dispatchEvent(new CustomEvent('prometheus-result',{detail:{...r,origin,job_id:id}}));}activity('idle');}
 setBusy(false);refreshHistory();
 adapt();
 if(result.state==='done'&&continueVoice&&voiceChat&&!document.hidden){
  if($('prompt').value.length>4000){stopVoiceChat(false);notice('This transcript is longer than one message. Edit it before sending; your words remain in the box.');return;}
  voiceState('Thinking…','thinking');await submit('chat');
 }else if(result.state==='done'&&origin==='local'&&result.result?.reply&&spokenMode&&!document.hidden)speak();
 }catch(error){if(job!==id)return;if(error.status===404||error.status===403){stopVoiceChat(false);job=null;localJob=null;setBusy(false);if(!$('prompt').value)$('prompt').value=lastSubmitted;notice('This request is no longer available. Check saved conversations before sending it again.');await refreshHistory();return;}notice('Connection interrupted. Your request may still be running; reconnecting…');setTimeout(()=>pollJob(id),5000);}
}
async function submitAction(body){
 if(busy||closed||!online||voiceChat||dictationPending||dictationJob)return false;invalidateHistory();stopSpeaking();notice('');submitting=true;setBusy(true);activity('thinking');partialBox=null;cancelling=false;
 try{const r=await api('/api/jobs',{prompt:'',...body});job=r.id;localJob=job;pollJob(job);return true;}catch(error){setBusy(false);activity('error');notice(error.message);return false;}finally{submitting=false;}
}
async function imageUpload(){
 const file=$('questionImage').files?.[0];
 if(!file||!file.size||file.size>8*1024*1024)throw new Error('Choose a PNG or JPEG image up to 8 MiB.');
 return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('Unable to read this image.'));reader.onload=()=>{const value=String(reader.result||''),comma=value.indexOf(',');if(comma<0||!value.slice(0,comma).endsWith(';base64'))return reject(new Error('Unable to read this image.'));resolve(value.slice(comma+1));};reader.readAsDataURL(file);});
}
async function submit(modeOverride){
 if(busy||closed||!online)return false;if(historyPending){notice('Wait for the selected conversation to load, or choose New conversation. Your message stays here.');return;}const mode=modeOverride||$('mode').value;let prompt=$('prompt').value.trim();if(mode==='reach'&&$('reachAction').value==='agent-resources'&&!prompt)prompt='all';if(!prompt&&!['dictate','tools','models','capabilities'].includes(mode)){$('prompt').focus();return;}
 window.dispatchEvent(new CustomEvent('prometheus-show-conversation'));invalidateHistory();stopSpeaking();notice('');submitting=true;cancelling=false;setBusy(true);activity(mode==='dictate'?'opening':'thinking');const captureEpoch=dictationEpoch;if(mode==='dictate'){dictationPending=true;voiceActivity='opening';voiceControls();notice(voiceChat?'Voice chat is on. Pause to send; End voice chat stops listening.':'Recording locally. Pause to finish, or press Stop recording. Your words will be held for review.');}
 try{const body={mode,prompt,profile:$('profile').value,provider:$('provider').value||'crossref',language:$('language').value||'Auto',project:$('project').value};if(mode==='reach')body.reach_action=$('reachAction').value;if(mode==='ask')body.source=$('evidenceSource').value||'documents';if(mode==='vision')body.content=await imageUpload();if(mode==='home')body.home_key=$('homeKey').value;if(mode==='documents')body.include_history=$('documentHistory')?.checked||false;if(session)body.session=session;const result=await api('/api/jobs',body);job=result.id;localJob=job;if(mode==='dictate'){dictationPending=false;if(captureEpoch!==dictationEpoch||document.hidden){discardedDictations.add(job);api('/api/cancel',{id:job}).catch(()=>{});pollJob(job);return false;}dictationJob=job;voiceControls();}partialBox=null;lastSubmitted=prompt;if(mode==='vision')$('questionImage').value='';if(!['dictate','tools','models','capabilities'].includes(mode)){append('user',prompt);$('prompt').value='';}pollJob(job);adapt();growPrompt();return true;}
 catch(error){stopVoiceChat();setBusy(false);activity('error');notice(error.message);return false;}finally{submitting=false;}
}
function stopSpeaking(){speechEpoch++;localVoice?.stop();if('speechSynthesis'in window)speechSynthesis.cancel();speaking=false;voiceActivity=null;if(voiceFrame!==null){cancelAnimationFrame(voiceFrame);voiceFrame=null;}$('stopSpeech').hidden=true;activity(busy?'thinking':'idle');}
async function speak(){
 if(!lastReply||busy||closed||document.hidden)return;
 stopSpeaking();const epoch=speechEpoch;
 if(!localVoice){notice('Local voice playback is unavailable. Your answer stays in text.');return;}
 $('stopSpeech').hidden=false;voiceState('Preparing voice · microphone off','preparing');notice('Preparing the installed local voice…');
 try{await localVoice.play(lastReply,api,()=>{if(epoch!==speechEpoch)return;speaking=true;voiceState('Prometheus is speaking · microphone off','speaking');if(voiceChat)voiceFrame=requestAnimationFrame(playbackWave);notice('');},()=>{if(epoch!==speechEpoch)return;speaking=false;voiceActivity=null;activity(busy?'thinking':'idle');$('stopSpeech').hidden=true;listenAfterReply();},error=>{if(epoch!==speechEpoch)return;stopVoiceChat();stopSpeaking();notice(error.message);});}
 catch(error){if(epoch!==speechEpoch)return;stopVoiceChat();stopSpeaking();notice(error.message);}
}
async function selectVoice(enabled){
 if(enabled&&spokenMode)return;
 if(!enabled)stopVoiceChat();stopSpeaking();const epoch=speechEpoch;spokenMode=false;
 $('textMode')?.setAttribute('aria-pressed','true');$('spokenMode')?.setAttribute('aria-pressed','false');
 if(enabled){try{if(!localVoice)throw Error('Local voice playback is unavailable.');await localVoice.unlock();if(epoch!==speechEpoch||document.hidden)return;spokenMode=true;}catch(error){if(epoch!==speechEpoch)return;notice(error.message);}}
 $('textMode')?.setAttribute('aria-pressed',String(!spokenMode));$('spokenMode')?.setAttribute('aria-pressed',String(spokenMode));voiceControls();
}
async function status(){if(closed)return;try{const s=await api('/api/status');online=true;$('runtimeVersion').textContent=typeof s.version==='string'&&s.version.trim()?s.version.trim():'Unavailable';$('lamp').className='ready';$('connection').textContent=s.development_preview?'Development preview · local test history':'Connected · local';hardware=s.hardware;$('memory').textContent=`${hardware.ram_gib} GiB installed · ${hardware.available_ram_gib??'unknown'} GiB available. ${hardware.chat_ready?'Local model can fit.':'Reference mode is ready.'}`;if(s.closing){stopVoiceChat(false);invalidateHistory();$('homeKey').value='';closed=true;setBusy(true);notice('Prometheus is closing after the current request. Saved conversations are retained.');stopSpeaking();motion();return;}if(s.job&&!s.job_background&&!job&&!submitting){invalidateHistory();job=s.job;pollJob(job);}setBusy(s.busy||submitting||!!job);if(!speaking)activity(voiceActivity||((submitting||job)&&!s.busy?'thinking':s.state));adapt();}catch(error){stopVoiceChat();invalidateHistory();online=false;setBusy(busy);$('lamp').className='';$('connection').textContent='Disconnected · retrying';activity('offline');}setTimeout(status,document.hidden?15000:5000);}
$('compose').onsubmit=event=>{event.preventDefault();submit();};$('prompt').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();submit();}};
$('mode').onchange=updateFields;$('reachAction').onchange=updateFields;$('evidenceSource').onchange=updateFields;$('quality').onchange=()=>{preference=$('quality').value;try{localStorage.setItem('prometheus.display',preference);}catch{}adapt();};
$('dictate').onclick=toggleRecording;$('finishRecording').onclick=finishRecording;$('codingTools').onclick=()=>submit('tools');$('stopSpeech').onclick=()=>{stopVoiceChat();stopSpeaking();};
$('newChat').onclick=()=>{stopVoiceChat();invalidateHistory();stopSpeaking();rememberSession(null);window.dispatchEvent(new CustomEvent('prometheus-show-conversation'));$('messages').replaceChildren();$('latestReply').hidden=true;$('history').value='';lastReply='';$('workLabel').textContent='Ready for your next idea';$('requestTime').textContent='';$('saveStatus').textContent='Ready · nothing sent';notice('New conversation. Earlier conversations remain in Saved conversations.');setBusy(busy);$('prompt').focus();};
$('history').onchange=async()=>{
 const chosen=$('history').value,selection=++historyEpoch;historyPending=false;
 if(!chosen||busy||closed||!online||voiceChat){setBusy(busy);return;}
 historyPending=true;setBusy(busy);notice('Loading saved conversation…');
 try{
  const rows=await api('/api/history/'+chosen);
  if(selection!==historyEpoch||closed||!online)return;
  stopSpeaking();window.dispatchEvent(new CustomEvent('prometheus-show-conversation'));$('messages').replaceChildren();rememberSession(chosen);
  for(const row of rows)append(row.role==='user'?'user':'assistant',row.content,row.label||'',row.details||null);
  const latest=[...rows].reverse().find(r=>r.role==='assistant');lastReply=latest?.content||'';
  $('workLabel').textContent=latest?.label||'Saved conversation';$('requestTime').textContent='';$('saveStatus').textContent='Saved locally';
  notice(rows.length===40?'Showing the latest 40 saved messages (display limit).':`Showing ${rows.length} saved message${rows.length===1?'':'s'}.`);
 }catch(error){if(selection===historyEpoch){$('history').value=session||'';notice(error.message);}}
 finally{if(selection===historyEpoch){historyPending=false;setBusy(busy);}}
};
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('mode').value=button.dataset.mode;updateFields();$('prompt').value=button.dataset.prompt;$('prompt').focus();});
$('close').onclick=async()=>{stopVoiceChat();invalidateHistory();$('homeKey').value='';$('questionImage').value='';try{const result=await api('/api/close',{});closed=true;stopSpeaking();setBusy(true);motion();$('connection').textContent=result.busy?'Finishing current request, then closing':'Prometheus closed';notice('The interface will release the drive after its current request. Use Windows safe removal before unplugging.');}catch(error){setBusy(busy);notice(error.message);}};
$('cancelRequest')?.addEventListener('click',async()=>{if(!job)return;stopVoiceChat(false);try{cancelling=true;$('cancelRequest').disabled=true;await api('/api/cancel',{id:job});notice('Stopping the current request. Partial generated text will not be saved.');}catch(error){cancelling=false;notice(error.message);}});
$('models').onclick=()=>submit('models');$('capabilities').onclick=()=>submit('capabilities');
$('textMode')?.addEventListener('click',()=>selectVoice(false));
$('spokenMode')?.addEventListener('click',()=>selectVoice(true));
$('ownerPanelToggle')?.addEventListener('click',()=>{const panel=$('ownerPanel');panel.hidden=!panel.hidden;$('ownerPanelToggle').setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)refreshHistory();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('ownerPanel').hidden){event.preventDefault();$('ownerPanel').hidden=true;$('ownerPanelToggle').setAttribute('aria-expanded','false');$('ownerPanelToggle').focus();}});
$('ownerLock')?.addEventListener('click',async()=>{stopVoiceChat();invalidateHistory();stopSpeaking();try{await api('/api/owner/lock',{});stopSpeaking();$('messages').replaceChildren();location.replace('/');}catch(error){setBusy(busy);notice(error.message);}});
window.addEventListener('pagehide',()=>{stopVoiceChat();stopSpeaking();localVoice?.suspend();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(voiceChat||dictationJob||dictationPending){voiceEndNotice='Voice chat ended when this tab was hidden. Start voice chat again when you return.';notice(voiceEndNotice);}stopVoiceChat();stopSpeaking();localVoice?.suspend();if($('ownerPanel'))$('ownerPanel').hidden=true;$('ownerPanelToggle')?.setAttribute('aria-expanded','false');}lastFrame=lastPaint=0;frames=slowFrames=drawTotal=0;adapt();});reduced.addEventListener('change',adapt);
async function init(){try{const b=await api('/api/bootstrap');token=b.token;for(const language of b.languages)$('language').add(new Option(language,language));const providers=Array.isArray(b.providers)?b.providers:(b.providers.providers||[]);for(const provider of providers)$('provider').add(new Option(provider.title||provider.name||provider.id,provider.id));await status();}catch(error){notice('Unable to connect: '+error.message);setTimeout(init,5000);}}
$('prompt').addEventListener('input',growPrompt);
setBusy(false);updateFields();activity('idle');motion();init();
