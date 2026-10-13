(function(){
'use strict';
const el=id=>document.getElementById(id),kinds=new Set(['integration','integration_access']);
const providerNames={agentmemory:'AgentMemory classic',agentmemory_service:'AgentMemory semantic service',browser_use:'Browser Use'};
const memoryAdapters=new Set(['agentmemory','agentmemory_service']);
const inputs=['integrationAdapter','integrationOperation','integrationProject','integrationContent','integrationQuery','integrationMemoryIds','integrationForgetReason','integrationUrl','integrationGrant','integrationTask','integrationScope','integrationHosts','integrationDuration','integrationAllowRemember','integrationAllowRecall','integrationAllowForget'];
let state={online:false,busy:false,closed:false,authLost:false},epoch=0,conversationEpoch=0,working=false,data=null,pending=null,decisionId=null,suggestedHost='',selectedTask=null;
const usable=()=>state.online&&!state.closed&&!state.authLost;
const say=text=>{el('integrationStatus').textContent=text;};
function node(tag,text,className=''){const result=document.createElement(tag);result.textContent=String(text??'');if(className)result.className=className;return result;}
function providerReady(){return data?.providers?.some(row=>row.provider===el('integrationAdapter').value&&row.available===true);}
function controls(){
 if(selectedTask&&selectedTask.session!==session){selectedTask=null;say('This conversation has no task selected here. Use a task below to resume it.');}
 const locked=!usable()||state.busy||working;
 for(const id of inputs)el(id).disabled=locked||!!pending;
 el('refreshIntegrations').disabled=!usable()||working;
 el('refreshIntegrationApproval').disabled=!usable()||working;
 el('reviewIntegration').disabled=locked||!!pending||!providerReady();
 el('reviewIntegrationAccess').disabled=locked||!!pending||!providerReady();
 el('approveIntegration').disabled=locked||!pending;
 el('rejectIntegration').disabled=locked||!pending;
 el('reviewIntegration').textContent=el('integrationGrant').value?'Run with approved task access':'Review action';
 for(const card of el('integrationGrants').children)for(const button of Array.from(card.children).filter(item=>item.tagName==='BUTTON'))
  button.disabled=!usable()||working||card.dataset.state!=='active'||button.dataset.action==='select'&&(state.busy||!!pending);
}
function renderReview(approval){
 pending=kinds.has(approval?.kind)?approval:null;
 const box=el('integrationReview');box.replaceChildren();box.hidden=!pending;el('integrationDecisions').hidden=!pending;
 if(pending){const review=pending.review;
  box.append(node('h3',review.action),node('p','Why: '+review.reason),node('p','Effects: '+review.effects),node('p','Access lasts: '+review.duration),
   node('pre',JSON.stringify(review.resources,null,2),'code-output'),node('p','This exact pending review expires in ten minutes. Say or type “yes, approve” in its conversation, or use Approve exactly this review.','fine'));
 }
 controls();
}
function clear(message){
 epoch++;working=false;data=null;decisionId=null;suggestedHost='';selectedTask=null;renderReview(null);
 for(const id of ['integrationProject','integrationContent','integrationQuery','integrationMemoryIds','integrationForgetReason','integrationUrl','integrationTask','integrationHosts'])el(id).value='';
 for(const id of ['integrationAllowRemember','integrationAllowRecall','integrationAllowForget'])el(id).checked=false;
 el('integrationProviders').replaceChildren();el('integrationGrants').replaceChildren();el('integrationResult').textContent='';
 el('integrationGrant').replaceChildren(new Option('Review this exact action first',''));say(message);controls();
}
function grantOptions(){
 const previous=el('integrationGrant').value;
 el('integrationGrant').replaceChildren(new Option('Review this exact action first',''));
 for(const grant of data?.grants||[])if(matches(grant))
  el('integrationGrant').add(new Option(grant.contract.task+' · '+grant.contract.actions.join(', '),grant.id));
 el('integrationGrant').value=previous;
}
function matches(grant){
 const task=grant.contract;if(grant.state!=='active'||task?.adapter!==el('integrationAdapter').value||!task.actions.includes(el('integrationOperation').value))return false;
 if(memoryAdapters.has(task.adapter))return task.resources.project===el('integrationProject').value.trim();
 try{const url=new URL(el('integrationUrl').value);return url.protocol==='https:'&&!url.username&&!url.password&&(task.resources.public_web===true||task.resources.hosts?.includes(url.hostname.toLowerCase().replace(/\.$/,'')));}catch{return false;}
}
function fields(reset=false){
 const browser=el('integrationAdapter').value==='browser_use';
 if(reset)el('integrationOperation').replaceChildren(...(browser?[new Option('Read a public page','read')]:[new Option('Remember a detail','remember'),new Option('Recall matching memories','recall'),new Option('Forget selected memories','forget')]));
 grantOptions();
 const operation=el('integrationOperation').value;
 el('integrationProjectField').hidden=browser;el('integrationContentField').hidden=browser||operation!=='remember';
 el('integrationQueryField').hidden=browser||operation!=='recall';el('integrationForgetField').hidden=browser||operation!=='forget';
 el('integrationUrlField').hidden=!browser;el('integrationMemoryActions').hidden=browser;el('integrationScopeField').hidden=!browser;el('integrationHostsField').hidden=!browser||el('integrationScope').value==='public-web';
 el('integrationHelp').textContent=browser?'Reads a public page in a temporary browser. No account profile is used. Links returned in the result require another read.':operation==='forget'?'Forgetting permanently removes only the selected IDs from this project. Recall first to inspect the IDs.':el('integrationAdapter').value==='agentmemory_service'?'Uses the full local service to recall notes by meaning. The service starts for this action and stops afterward. Semantic notes are separate from classic project notes.':'Project memory stays on your portable data drive. Choose a project name such as home or workshop.';
 el('integrationDurationHelp').textContent=el('integrationDuration').value==='until-revoked'?'Until revoked: this access remains active until you use Revoke below.':'Access expires after the selected duration, measured from approval. You can revoke it sooner.';
 controls();
}
function renderInventory(){
 el('integrationProviders').replaceChildren(...data.providers.map(row=>{
  const card=node('div','', 'workspace-card');
  card.append(node('strong',(providerNames[row.provider]||row.name||row.provider)+' · '+(row.available?'Available':'Unavailable')),
   node('p','Version: '+(row.version||'unknown')+' · Integrity: '+String(row.integrity??'not checked'),'fine'),
   node('p','Actions: '+(row.operations||[]).join(', '),'fine'));
  if(row.limitations)card.append(node('p',Array.isArray(row.limitations)?row.limitations.join(' '):row.limitations,'fine'));
  return card;
 }));
 el('integrationGrants').replaceChildren(...data.grants.map(grant=>{
  const task=grant.contract||{task:'Task access needs a new review',adapter:grant.capability||'Unavailable',actions:[],resources:{}},card=node('div','','workspace-card');card.dataset.state=grant.state;
  card.append(node('strong',task.task+' · '+grant.state),node('p',task.adapter+' · '+task.actions.join(', ')),node('pre',JSON.stringify(task.resources,null,2),'code-output'),
   node('p',grant.expires_at===null?'Until you revoke it':'Expires: '+new Date(grant.expires_at*1000).toLocaleString(),'fine'));
  const revoke=node('button','Revoke task access');revoke.disabled=grant.state!=='active';
  revoke.onclick=()=>request(async valid=>{
   await api('/api/integrations/revoke',{id:grant.id});if(!valid())return;
   if(selectedTask?.id===grant.id)selectedTask=null;if(el('integrationGrant').value===grant.id)el('integrationGrant').value='';
   say('Task access revoked. A running action using it has been asked to stop.');await inventory(valid);
  });
  const use=node('button','Use in this conversation');use.dataset.action='select';
  use.onclick=()=>{
   if(state.busy||pending)return;
   const conversation=session,selectionEpoch=conversationEpoch;
   return request(async valid=>{
    selectedTask=null;say('Selecting task for this conversation…');
    const result=await api('/api/integrations/select',{id:grant.id,...(conversation?{session:conversation}:{})});
    if(!valid())return;
    if(session!==conversation||selectionEpoch!==conversationEpoch){say('Conversation changed. Select task access here to continue.');return;}
    rememberSession(result.session);selectedTask={id:result.id,contract:result.contract,session:result.session};
    say('Using task: '+result.contract.task);controls();
   });
  };
  card.append(revoke,use);return card;
 }));
 if(!data.grants.length)el('integrationGrants').append(node('p','No task access has been issued for this owner.','fine'));
 grantOptions();controls();
}
async function request(callback){
 if(!usable()||working)return;working=true;const current=epoch;controls();
 try{await callback(()=>current===epoch&&usable());}
 catch(error){if(current===epoch)say(error.message||'Integration request failed. No action was replayed.');}
 finally{if(current===epoch){working=false;controls();}}
}
async function inventory(valid){
 const result=await api('/api/integrations');if(!valid())return;
 data={providers:Array.isArray(result.providers)?result.providers:[],grants:Array.isArray(result.grants)?result.grants:[]};renderInventory();
 if(selectedTask&&!data.grants.some(grant=>grant.id===selectedTask.id&&grant.state==='active')){selectedTask=null;say('The selected task is no longer active. Choose active task access to continue.');}
}
async function pendingReview(valid){
 const result=await api('/api/approvals/pending');if(!valid())return;
 renderReview(result.approval);
 say(pending?'Review the exact integration proposal below.':result.approval?'An application change is awaiting approval in Maintenance.':selectedTask?'Using task: '+selectedTask.contract.task:'No pending integration approval.');
}
function load(){return request(async valid=>{await inventory(valid);if(valid())await pendingReview(valid);});}
function project(){const value=el('integrationProject').value.trim();if(!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value))throw Error('Use a project name of 1 to 64 lowercase letters, digits, underscores or hyphens.');return value;}
function actionBody(){
 const adapter=el('integrationAdapter').value,operation=el('integrationOperation').value;let args;
 if(adapter==='browser_use'){
  const url=el('integrationUrl').value.trim();if(!url)throw Error('Enter the public page URL.');
  const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.username||parsed.password)throw Error('Use a public HTTPS URL without account credentials.');args={url};
 }else if(operation==='remember'){const content=el('integrationContent').value.trim();if(!content)throw Error('Write the exact detail to remember.');args={project:project(),content};}
 else if(operation==='recall'){const query=el('integrationQuery').value.trim();if(!query)throw Error('Enter what to recall.');args={project:project(),query};}
 else{const memory_ids=el('integrationMemoryIds').value.split(/[\s,]+/).filter(Boolean),reason=el('integrationForgetReason').value.trim();if(!memory_ids.length||!reason)throw Error('Choose memory IDs from a recall result and explain why they should be forgotten.');args={project:project(),memory_ids,reason};}
 const body={mode:'integration',adapter,operation,arguments:args};if(el('integrationGrant').value)body.task_grant=el('integrationGrant').value;return body;
}
function taskBody(){
 const description=el('integrationTask').value.trim();if(!description)throw Error('Describe the task this access is for.');
 const adapter=el('integrationAdapter').value,browser=adapter==='browser_use';
 const actions=browser?['read']:[['Remember','remember'],['Recall','recall'],['Forget','forget']].filter(([id])=>el('integrationAllow'+id).checked).map(([,action])=>action);
 if(!actions.length)throw Error('Select at least one action to allow.');
 const publicWeb=el('integrationScope').value==='public-web',hosts=[...new Set(el('integrationHosts').value.trim().toLowerCase().split(/[\s,]+/).filter(Boolean))];
 if(browser&&!publicWeb&&(!hosts.length||hosts.some(host=>!/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/.test(host))))throw Error('Enter exact public hostnames, without URLs or wildcards.');
 const bytes=crypto.getRandomValues(new Uint8Array(16)),task_id=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
 const duration=el('integrationDuration').value;
 return {mode:'integration_access',prompt:description,task:{task_id,task:description,adapter,actions,resources:browser?(publicWeb?{public_web:true}:{hosts}):{project:project()},duration_seconds:duration==='until-revoked'?null:Number(duration)}};
}
async function submit(build){
 if(!usable()||working||state.busy||pending||!providerReady())return;
 let body;try{body=build();}catch(error){say(error.message);return;}
 await request(async valid=>{const accepted=await submitAction({...body,...(session?{session}:{})});if(valid()&&!accepted)say('The request was not started. Check the connection and try again.');});
}
async function decide(decision){
 if(!usable()||working||state.busy||!pending)return;
 const selected=pending;decisionId=selected.id;
 await request(async valid=>{const accepted=await submitAction({mode:'approval_decision',approval_decision:decision,approval_id:selected.id,approval_fingerprint:selected.fingerprint,session:selected.session});if(valid()&&!accepted){decisionId=null;say('Approval was not submitted. Review the current state before trying again.');}});
}
el('reviewIntegration').onclick=()=>submit(actionBody);el('reviewIntegrationAccess').onclick=()=>submit(taskBody);
el('approveIntegration').onclick=()=>decide('approve');el('rejectIntegration').onclick=()=>decide('reject');
el('refreshIntegrations').onclick=load;el('refreshIntegrationApproval').onclick=()=>request(pendingReview);
el('integrationAdapter').onchange=()=>fields(true);el('integrationOperation').onchange=()=>fields();el('integrationDuration').onchange=()=>fields();el('integrationScope').onchange=()=>fields();el('integrationGrant').onchange=controls;
el('integrationProject').oninput=()=>{grantOptions();controls();};
el('integrationUrl').oninput=()=>{
 if(!el('integrationHosts').value.trim()||el('integrationHosts').value===suggestedHost){try{suggestedHost=new URL(el('integrationUrl').value).hostname;el('integrationHosts').value=suggestedHost;}catch{}}
 grantOptions();controls();
};
el('tabIntegrations').addEventListener('click',load);el('tabIntegrations').addEventListener('focus',()=>{if(!data)load();});
el('ownerLock').addEventListener('click',()=>{state.authLost=true;clear('Owner lock requested. Unlock to use integrations.');});
window.addEventListener('prometheus-auth-lost',()=>{state.authLost=true;clear('Owner session ended. Unlock to use integrations.');});
window.addEventListener('prometheus-show-conversation',()=>{conversationEpoch++;controls();});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};if(!usable())clear(state.closed?'Prometheus is closed.':'Local connection unavailable.');controls();});
window.addEventListener('prometheus-result',event=>{
 const result=event.detail;if(result.origin!=='local'||!usable())return;
 if(result.details?.approval){if(kinds.has(result.details.approval.kind)){renderReview(result.details.approval);say('Review the exact action, resources and duration before approval.');}else renderReview(null);return;}
 const resolved=result.details?.approval_resolved,ownResolution=resolved&&(pending?.id===resolved||decisionId===resolved);
 const relevant=ownResolution||kinds.has(result.action)||result.details?.integration||typeof result.details?.task_grant==='object'&&result.details?.task_grant;
 if(result.error&&(kinds.has(result.action)||result.action==='approval_decision'&&decisionId)){if(decisionId){decisionId=null;renderReview(null);}say(result.error+' Refresh pending review before trying again.');return;}
 if(!relevant)return;
 if(ownResolution){decisionId=null;renderReview(null);}
 say(result.reply||'Integration request completed.');
 if(result.details?.integration)el('integrationResult').textContent=JSON.stringify(result.details.integration,null,2).slice(0,24000);
 if(result.details?.task_grant&&typeof result.details.task_grant==='object'){
  // Results can arrive before submitAction resolves; a busy request must not
  // drop the issued grant. Update the local snapshot without another dispatch.
  const grant=result.details.task_grant;selectedTask={id:grant.id,contract:grant.contract,session:result.session||session};if(data){data.grants=data.grants.filter(row=>row.id!==grant.id);data.grants.push(grant);renderInventory();}say('Using task: '+grant.contract.task);
 }else if(typeof result.details?.task_grant==='string'){
  const grant=data?.grants.find(row=>row.id===result.details.task_grant);if(grant){selectedTask={id:grant.id,contract:grant.contract,session:result.session||session};say('Using task: '+grant.contract.task);}
 }
});
fields(true);
})();
