(function(){
'use strict';
const el=id=>document.getElementById(id),box=el('decisionHistory'),list=el('decisionHistoryList'),status=el('decisionHistoryStatus'),refresh=el('decisionHistoryRefresh');
if(!box||!list||!status||!refresh)return;
let state={online:false,busy:false,closed:false},generation=0,reading=false;
const ready=()=>state.online&&!state.busy&&!state.closed&&!document.hidden;
const labels={runtime_config:'Runtime settings',project_edit:'Project change',project_task:'Project task',idle_config:'Research settings',self_improve:'Application improvement',scheduled_syntax:'Scheduled check',remote_syntax:'Remote check',integration:'Integration',integration_access:'Integration access'};
const decisions={approve:'Approved',reject:'Denied',request_changes:'Changes requested'};
function clear(message){generation++;reading=false;list.replaceChildren();status.textContent=message;controls();}
function controls(){refresh.disabled=!ready()||reading;}
async function load(){
 if(!ready()||!box.open||reading)return;
 const version=generation;reading=true;controls();
 try{
  const data=await api('/api/decisions/history');if(version!==generation||!ready()||!box.open)return;
  if(!data||data.schema_version!==1||data.read_only!==true||!Array.isArray(data.items)||data.items.length>50)throw Error('Invalid history');
  for(const row of data.items)if(!row||!['id','fingerprint','session','kind','feedback'].every(k=>typeof row[k]==='string')||!Object.prototype.hasOwnProperty.call(decisions,row.decision)||row.feedback.length>2000||!Number.isFinite(row.decided_at)||row.decided_at<0)throw Error('Invalid receipt');
  list.replaceChildren();for(const row of data.items){const item=document.createElement('li'),title=document.createElement('p');title.textContent=(Object.prototype.hasOwnProperty.call(labels,row.kind)?labels[row.kind]:'Owner decision')+' · '+decisions[row.decision]+' · '+new Date(row.decided_at*1000).toLocaleString();item.append(title);if(row.feedback){const note=document.createElement('p');note.textContent=row.feedback;item.append(note);}list.append(item);}
  status.textContent=data.items.length+' saved decisions. History does not authorize or resume work.';
 }catch(error){if(version===generation)clear('Decision history unavailable. No historical record authorizes work.');}
 finally{if(version===generation){reading=false;controls();}}
}
refresh.onclick=()=>load();box.addEventListener('toggle',()=>{if(box.open)void load();else clear('Open to read saved decisions. History does not authorize work.');});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};if(!ready())clear('Decision history paused or unavailable. Reopen or refresh when ready.');controls();});
window.addEventListener('prometheus-auth-lost',()=>{state.online=false;clear('Unlock to read owner decision history.');});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clear('Decision history paused while hidden.');controls();});
controls();
})();
