'use strict';
const $=id=>document.getElementById(id);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const policy=new PrometheusAdaptive.AdaptiveDisplay();
let token='',session=null,job=null,localJob=null,busy=false,closed=false,online=false,lastReply='',speaking=false,partialBox=null,lastSubmitted='',submitting=false,cancelling=false;
let historyEpoch=0,historyPending=false;
const transport=new PrometheusTransport.LocalClient();
transport.onRestart=()=>{invalidateHistory();$('homeKey').value='';$('questionImage').value='';job=null;localJob=null;cancelling=false;if(partialBox)partialBox.querySelector('.label').textContent='Interrupted by restart · not saved';partialBox=null;if(!$('prompt').value)$('prompt').value=lastSubmitted;notice('Prometheus restarted. Saved conversations are retained; an unfinished request was not resubmitted.');refreshHistory();};
let hardware={},display={level:1,name:'Simple',fps:15,size:0},raf=null,lastPaint=0,paintCost=0,lag=0,frames=0,slowFrames=0,drawTotal=0,lastFrame=0;
let preference='auto';try{preference=localStorage.getItem('prometheus.display')||'auto';session=null;}catch{}
if(!['auto','simple','still'].includes(preference))preference='auto';$('quality').value=preference;
const canvas=$('fluid');
const orbRenderer=window.PrometheusOrb?new window.PrometheusOrb.FoldedCore(canvas):null;
const localVoice=window.PrometheusVoice?new window.PrometheusVoice.LocalVoice():null;
let spokenMode=false,speechEpoch=0;
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function rememberSession(value){session=value;try{if(value)localStorage.setItem('prometheus.session',value);else localStorage.removeItem('prometheus.session');}catch{}}
function invalidateHistory(){historyEpoch++;historyPending=false;$('history').value=session||'';}
function activity(state){$('orb').dataset.state=state;const labels={idle:['I’m here.','What shall we work on?'],thinking:['Thinking…','Working on your request'],listening:['Listening…','Recording eight seconds'],speaking:['Speaking…','Reading your answer'],error:['Let’s adjust.','See the message below'],offline:['Disconnected','Your text stays here']};const pair=labels[state]||labels.idle;$('activity').textContent=pair[0];$('activityNote').textContent=pair[1];}
async function api(path,data){try{return await transport.request(path,data);}catch(error){if(error.status===401){window.dispatchEvent(new CustomEvent('prometheus-auth-lost'));invalidateHistory();stopSpeaking();$('messages').replaceChildren();location.replace('/');}throw error;}}
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
function append(role,text,label='',details=null){$('welcome')?.remove();const box=document.createElement('article');box.className='message '+role;const tag=document.createElement('div');tag.className='label';tag.textContent=label||(role==='user'?'You':'Prometheus');const content=document.createElement('div');content.className='text';if(role==='assistant')renderSourceLinks(content,text);else content.textContent=text;box.append(tag,content);if(details){const panel=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Sources and result details';pre.textContent=JSON.stringify(details,null,2);panel.append(summary,pre);box.append(panel);}const messages=$('messages');messages.append(box);while(messages.children.length>80)messages.firstChild.remove();messages.scrollTop=messages.scrollHeight;return box;}
function setBusy(value){busy=value;for(const id of ['send','dictate','newChat','history','codingTools','models','capabilities','homeKey','questionImage'])$(id).disabled=value||closed||!online||(historyPending&&!['newChat','history'].includes(id));window.dispatchEvent(new CustomEvent('prometheus-busy',{detail:{busy,closed,online}}));if(!busy&&$('cancelRequest'))$('cancelRequest').hidden=true;}
function updateFields(){const mode=$('mode').value;$('reachField').hidden=mode!=='reach';$('homeKeyField').hidden=mode!=='home';$('sourceField').hidden=mode!=='ask';$('imageField').hidden=mode!=='vision';$('profileField').hidden=mode!=='library'&&!(mode==='ask'&&$('evidenceSource').value==='offline');$('providerField').hidden=mode!=='research';$('languageField').hidden=mode!=='coding';$('projectField').hidden=mode!=='project'&&!(mode==='ask'&&$('evidenceSource').value==='project');$('prompt').placeholder=mode==='reach'?({web:'https://example.com/article',feed:'https://example.com/feed.xml','github-repo':'Panniantong/Agent-Reach','github-releases':'Panniantong/Agent-Reach','github-search':'portable local AI',youtube:'https://www.youtube.com/watch?v=…','youtube-transcript':'https://www.youtube.com/watch?v=…'}[$('reachAction').value]):mode==='ask'?'Ask a question about your selected sources…':mode==='vision'?'What would you like to know about this image?':mode==='home'?'For example: turn off bedroom lights; status front porch lights':mode==='calculate'?'For example: (24 * 17) / 3':mode==='project'?'Search for a function, class, or phrase…':mode==='coding'?'Describe what you want to build or fix…':'Speak your mind…';}
function adapt(){const previous=display.level;display=policy.update({free:hardware.available_ram_gib,threads:hardware.cpu_threads||2,busy,cost:paintCost,lag,hidden:document.hidden,reduced:reduced.matches,preference},performance.now());if(previous!==display.level){paintCost=lag=frames=slowFrames=drawTotal=0;orbRenderer?.configure(display.level);}else if(orbRenderer)orbRenderer.level=display.level;$('tier').textContent=display.name;$('qualityStatus').textContent=display.name+' · '+display.reason;motion();}
function render(now){
 raf=null;if(closed||document.hidden||display.fps===0)return;
 const interval=1000/display.fps;
 if(now-lastPaint>=interval-1){const begin=performance.now();orbRenderer?.draw(now,$('orb').dataset.state,localVoice?.level(now)||0);drawTotal+=performance.now()-begin;frames++;if(lastPaint&&now-lastPaint>interval*1.8)slowFrames++;lastPaint=now;}
 if(!lastFrame)lastFrame=now;if(now-lastFrame>=2000){paintCost=frames?drawTotal/frames:0;lag=frames?slowFrames/frames:0;frames=slowFrames=drawTotal=0;lastFrame=now;adapt();return;}
 raf=requestAnimationFrame(render);
}
function motion(){if(raf!==null){cancelAnimationFrame(raf);raf=null;}if(!closed&&!document.hidden&&display.fps>0)raf=requestAnimationFrame(render);else orbRenderer?.pause();}
async function refreshHistory(){try{const rows=await api('/api/sessions');const selected=historyPending?$('history').value:session;$('history').replaceChildren(new Option('Choose a conversation…',''));for(const row of rows){const when=new Date(row.created_at).toLocaleString();$('history').add(new Option(`${when} · ${row.turn_count} turns`,row.session_id));}if(selected)$('history').value=selected;}catch(error){notice(error.message);}}
async function pollJob(id){
 if(job!==id||closed)return;
 try{const result=await api('/api/job/'+id);if(job!==id)return;if(result.state==='running'){$('activityNote').textContent=result.stage||'Working on your request';if($('cancelRequest')){$('cancelRequest').hidden=!result.cancellable;$('cancelRequest').disabled=cancelling;}if(result.partial){if(!partialBox)partialBox=append('assistant','','In progress · not saved');partialBox.querySelector('.text').textContent=result.partial;window.dispatchEvent(new CustomEvent('prometheus-partial',{detail:{text:result.partial}}));}setTimeout(()=>pollJob(id),document.hidden?5000:350);return;}
 const origin=localJob===id?'local':'observed';if(localJob===id)localJob=null;job=null;cancelling=false;const completedPartial=partialBox;partialBox=null;$('requestTime').textContent=result.seconds+' s';
 if(result.state==='error'||result.state==='cancelled'){if(completedPartial)completedPartial.querySelector('.label').textContent='Incomplete · not saved';append('error',result.error,result.state==='cancelled'?'Request stopped':'Request needs attention');activity('error');if(!$('prompt').value)$('prompt').value=lastSubmitted;window.dispatchEvent(new CustomEvent('prometheus-result',{detail:{action:result.action,error:result.error,origin,job_id:id}}));notice('You can edit your request and try again.');}
 else{const r=result.result;if(completedPartial)completedPartial.remove();if(r.transcript){$('prompt').value=r.transcript;notice('Dictation is ready. Review it, then send when you’re ready.');}else{lastReply=r.reply||'';append('assistant',lastReply,r.label,r.details);if(r.session)rememberSession(r.session);$('workLabel').textContent=r.label||'Request completed';$('saveStatus').textContent=r.session?'Saved locally':'Completed locally';$('speak').disabled=!lastReply;window.dispatchEvent(new CustomEvent('prometheus-result',{detail:{...r,origin,job_id:id}}));}activity('idle');}
 setBusy(false);refreshHistory();
 adapt();if(result.state==='done'&&origin==='local'&&result.result?.reply&&spokenMode&&!document.hidden)speak();
 }catch(error){if(job!==id)return;if(error.status===404||error.status===403){job=null;localJob=null;setBusy(false);if(!$('prompt').value)$('prompt').value=lastSubmitted;notice('This request is no longer available. Check saved conversations before sending it again.');await refreshHistory();return;}notice('Connection interrupted. Your request may still be running; reconnecting…');setTimeout(()=>pollJob(id),5000);}
}
async function submitAction(body){
 if(busy||closed||!online)return false;invalidateHistory();stopSpeaking();notice('');submitting=true;setBusy(true);activity('thinking');partialBox=null;cancelling=false;
 try{const r=await api('/api/jobs',{prompt:'',...body});job=r.id;localJob=job;pollJob(job);return true;}catch(error){setBusy(false);activity('error');notice(error.message);return false;}finally{submitting=false;}
}
async function imageUpload(){
 const file=$('questionImage').files?.[0];
 if(!file||!file.size||file.size>8*1024*1024)throw new Error('Choose a PNG or JPEG image up to 8 MiB.');
 return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('Unable to read this image.'));reader.onload=()=>{const value=String(reader.result||''),comma=value.indexOf(',');if(comma<0||!value.slice(0,comma).endsWith(';base64'))return reject(new Error('Unable to read this image.'));resolve(value.slice(comma+1));};reader.readAsDataURL(file);});
}
async function submit(modeOverride){
 if(busy||closed||!online)return;if(historyPending){notice('Wait for the selected conversation to load, or choose New conversation. Your message stays here.');return;}const mode=modeOverride||$('mode').value,prompt=$('prompt').value.trim();if(!prompt&&!['dictate','tools','models','capabilities'].includes(mode)){$('prompt').focus();return;}
 invalidateHistory();stopSpeaking();notice('');submitting=true;cancelling=false;setBusy(true);activity(mode==='dictate'?'listening':'thinking');if(mode==='dictate')notice('Microphone on for eight seconds. Speech is transcribed locally and held for your review.');
 try{const body={mode,prompt,profile:$('profile').value,provider:$('provider').value||'crossref',language:$('language').value||'Auto',project:$('project').value};if(mode==='reach')body.reach_action=$('reachAction').value;if(mode==='ask')body.source=$('evidenceSource').value||'documents';if(mode==='vision')body.content=await imageUpload();if(mode==='home')body.home_key=$('homeKey').value;if(mode==='documents')body.include_history=$('documentHistory')?.checked||false;if(session)body.session=session;const result=await api('/api/jobs',body);job=result.id;localJob=job;partialBox=null;lastSubmitted=prompt;if(mode==='vision')$('questionImage').value='';if(!['dictate','tools','models','capabilities'].includes(mode)){append('user',prompt);$('prompt').value='';}pollJob(job);adapt();}
 catch(error){setBusy(false);activity('error');notice(error.message);}finally{submitting=false;}
}
function stopSpeaking(){speechEpoch++;localVoice?.stop();if('speechSynthesis'in window)speechSynthesis.cancel();speaking=false;$('stopSpeech').hidden=true;activity(busy?'thinking':'idle');}
async function speak(){
 if(!lastReply||busy||closed||document.hidden)return;
 stopSpeaking();const epoch=speechEpoch;
 if(!localVoice){notice('Local voice playback is unavailable. Your answer stays in text.');return;}
 $('stopSpeech').hidden=false;notice('Preparing the installed local voice…');
 try{await localVoice.play(lastReply,api,()=>{if(epoch!==speechEpoch)return;speaking=true;activity('speaking');notice('');},()=>{if(epoch!==speechEpoch)return;speaking=false;activity(busy?'thinking':'idle');$('stopSpeech').hidden=true;});}
 catch(error){if(epoch!==speechEpoch)return;stopSpeaking();notice(error.message);}
}
async function selectVoice(enabled){
 stopSpeaking();const epoch=speechEpoch;spokenMode=false;
 $('textMode')?.setAttribute('aria-pressed','true');$('spokenMode')?.setAttribute('aria-pressed','false');
 if(enabled){try{if(!localVoice)throw Error('Local voice playback is unavailable.');await localVoice.unlock();if(epoch!==speechEpoch||document.hidden)return;spokenMode=true;}catch(error){if(epoch!==speechEpoch)return;notice(error.message);}}
 $('textMode')?.setAttribute('aria-pressed',String(!spokenMode));$('spokenMode')?.setAttribute('aria-pressed',String(spokenMode));
}
async function status(){if(closed)return;try{const s=await api('/api/status');online=true;$('lamp').className='ready';$('connection').textContent=s.development_preview?'Development preview · local test history':'Connected · local';hardware=s.hardware;$('memory').textContent=`${hardware.ram_gib} GiB installed · ${hardware.available_ram_gib??'unknown'} GiB available. ${hardware.chat_ready?'Local model can fit.':'Reference mode is ready.'}`;if(s.closing){invalidateHistory();$('homeKey').value='';closed=true;setBusy(true);notice('Prometheus is closing after the current request. Saved conversations are retained.');stopSpeaking();motion();return;}if(s.job&&!job&&!submitting){invalidateHistory();job=s.job;pollJob(job);}setBusy(s.busy||submitting||!!job);if(!speaking)activity((submitting||job)&&!s.busy?"thinking":s.state);adapt();}catch(error){invalidateHistory();online=false;setBusy(busy);$('lamp').className='';$('connection').textContent='Disconnected · retrying';activity('offline');}setTimeout(status,document.hidden?15000:5000);}
$('compose').onsubmit=event=>{event.preventDefault();submit();};$('prompt').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();submit();}};
$('mode').onchange=updateFields;$('reachAction').onchange=updateFields;$('evidenceSource').onchange=updateFields;$('quality').onchange=()=>{preference=$('quality').value;try{localStorage.setItem('prometheus.display',preference);}catch{}adapt();};
$('dictate').onclick=()=>submit('dictate');$('codingTools').onclick=()=>submit('tools');$('speak').onclick=speak;$('stopSpeech').onclick=stopSpeaking;
$('newChat').onclick=()=>{invalidateHistory();stopSpeaking();rememberSession(null);$('messages').replaceChildren();$('history').value='';lastReply='';$('speak').disabled=true;$('workLabel').textContent='Ready for your next idea';$('requestTime').textContent='';$('saveStatus').textContent='Ready · nothing sent';notice('New conversation. Earlier conversations remain in Saved conversations.');setBusy(busy);$('prompt').focus();};
$('history').onchange=async()=>{
 const chosen=$('history').value,selection=++historyEpoch;historyPending=false;
 if(!chosen||busy||closed||!online){setBusy(busy);return;}
 historyPending=true;setBusy(busy);notice('Loading saved conversation…');
 try{
  const rows=await api('/api/history/'+chosen);
  if(selection!==historyEpoch||closed||!online)return;
  stopSpeaking();$('messages').replaceChildren();rememberSession(chosen);
  for(const row of rows)append(row.role==='user'?'user':'assistant',row.content,row.label||'',row.details||null);
  const latest=[...rows].reverse().find(r=>r.role==='assistant');lastReply=latest?.content||'';
  $('speak').disabled=!lastReply;$('workLabel').textContent=latest?.label||'Saved conversation';$('requestTime').textContent='';$('saveStatus').textContent='Saved locally';
  notice(rows.length===40?'Showing the latest 40 saved messages (display limit).':`Showing ${rows.length} saved message${rows.length===1?'':'s'}.`);
 }catch(error){if(selection===historyEpoch){$('history').value=session||'';notice(error.message);}}
 finally{if(selection===historyEpoch){historyPending=false;setBusy(busy);}}
};
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>{$('mode').value=button.dataset.mode;updateFields();$('prompt').value=button.dataset.prompt;$('prompt').focus();});
$('close').onclick=async()=>{invalidateHistory();$('homeKey').value='';$('questionImage').value='';try{const result=await api('/api/close',{});closed=true;stopSpeaking();setBusy(true);motion();$('connection').textContent=result.busy?'Finishing current request, then closing':'Prometheus closed';notice('The interface will release the drive after its current request. Use Windows safe removal before unplugging.');}catch(error){setBusy(busy);notice(error.message);}};
$('cancelRequest')?.addEventListener('click',async()=>{if(!job)return;try{cancelling=true;$('cancelRequest').disabled=true;await api('/api/cancel',{id:job});notice('Stopping the current request. Partial generated text will not be saved.');}catch(error){cancelling=false;notice(error.message);}});
$('models').onclick=()=>submit('models');$('capabilities').onclick=()=>submit('capabilities');
$('textMode')?.addEventListener('click',()=>selectVoice(false));
$('spokenMode')?.addEventListener('click',()=>selectVoice(true));
$('ownerPanelToggle')?.addEventListener('click',()=>{const panel=$('ownerPanel');panel.hidden=!panel.hidden;$('ownerPanelToggle').setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)refreshHistory();});
$('ownerLock')?.addEventListener('click',async()=>{invalidateHistory();stopSpeaking();try{await api('/api/owner/lock',{});stopSpeaking();$('messages').replaceChildren();location.replace('/');}catch(error){setBusy(busy);notice(error.message);}});
window.addEventListener('pagehide',()=>{stopSpeaking();localVoice?.suspend();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){stopSpeaking();localVoice?.suspend();if($('ownerPanel'))$('ownerPanel').hidden=true;$('ownerPanelToggle')?.setAttribute('aria-expanded','false');}lastFrame=lastPaint=0;frames=slowFrames=drawTotal=0;adapt();});reduced.addEventListener('change',adapt);
async function init(){try{const b=await api('/api/bootstrap');token=b.token;for(const language of b.languages)$('language').add(new Option(language,language));const providers=Array.isArray(b.providers)?b.providers:(b.providers.providers||[]);for(const provider of providers)$('provider').add(new Option(provider.title||provider.name||provider.id,provider.id));await status();}catch(error){notice('Unable to connect: '+error.message);setTimeout(init,5000);}}
setBusy(false);updateFields();activity('idle');motion();init();
