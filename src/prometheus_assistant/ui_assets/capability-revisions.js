(function(){
'use strict';
const el=id=>document.getElementById(id),box=el('capabilityRevision'),status=el('capabilityRevisionStatus'),proposal=el('capabilityRevisionProposal'),feedback=el('capabilityRevisionFeedback'),refresh=el('capabilityRevisionRefresh'),submit=el('capabilityRevisionSubmit');
if(!box||!status||!proposal||!feedback||!refresh||!submit)return;
const modes={runtime_config:'runtime_config',idle_config:'idle_config',integration:'integration',integration_access:'integration_access',remote_syntax_prepare:'remote_syntax',scheduled_syntax_prepare:'scheduled_syntax'};
const labels={runtime_config:'Runtime settings',idle_config:'Research settings',integration:'Integration action',integration_access:'Integration access',remote_syntax:'Remote check',scheduled_syntax:'Scheduled check'};
const identity=(value,length)=>typeof value==='string'&&new RegExp('^[a-f0-9]{'+length+'}$').test(value);
const currentSession=()=>typeof session==='string'?session:null;
let state={busy:false,online:false,closed:false},epoch=0,readVersion=0,reading=false,sending=false,pending=null,revision=null,reservation=null,inflight=null;
const tickets=new WeakMap();
const ready=()=>state.online&&!state.busy&&!state.closed&&!document.hidden;
function controls(){refresh.disabled=!ready()||reading||sending;feedback.disabled=!ready()||!pending||sending;submit.disabled=!ready()||!pending||sending;}
function say(text){status.textContent=text;}
function render(){
 proposal.replaceChildren();proposal.hidden=!pending;
 if(pending){for(const text of [labels[pending.kind]+' · review '+pending.id,'Fingerprint: '+pending.fingerprint,'Conversation: '+pending.session]){const p=document.createElement('p');p.textContent=text;proposal.append(p);}
  const detail=document.createElement('pre');detail.textContent=JSON.stringify(pending.review||{},null,2);proposal.append(detail);}
 controls();
}
function clear(message){epoch++;readVersion++;reading=false;sending=false;pending=null;revision=null;reservation=null;inflight=null;feedback.value='';render();say(message);}
function validApproval(value){return value&&Object.prototype.hasOwnProperty.call(labels,value.kind)&&identity(value.id,32)&&identity(value.fingerprint,64)&&identity(value.session,32)&&value.session===currentSession();}
function review(value){
 pending=validApproval(value)?value:null;feedback.value='';
 if(pending){revision=null;reservation=null;say('Describe changes to this exact '+labels[pending.kind].toLowerCase()+' proposal.');}
 else if(!revision)say(value?'This proposal is not a supported capability in the current conversation.':'No pending capability proposal in this conversation.');
 render();
}
async function load(){
 if(!ready()||reading||sending||!box.open)return;
 const generation=readVersion,conversation=currentSession();reading=true;controls();
 try{const data=await api('/api/approvals/pending');if(generation!==readVersion||conversation!==currentSession()||!ready()||!box.open)return;review(data?.approval);}
 catch{if(generation===readVersion){pending=null;render();say('Pending proposal unavailable. Refresh before requesting changes.');}}
 finally{if(generation===readVersion){reading=false;controls();}}
}
function withContext(body){
 readVersion++;reading=false;
 const request={...body};delete request.capability_revision_token;
 const kind=Object.prototype.hasOwnProperty.call(modes,request.mode)?modes[request.mode]:null,conversation=currentSession();
 let reserved=null;
 if(revision&&revision.session!==conversation){revision=null;reservation=null;say('Conversation changed. Prepare a new review.');}
 if(kind&&revision&&request.session===conversation&&revision.session===request.session&&revision.kind===kind){
  if(reservation)throw Error('Wait for the current replacement request before preparing another.');
  reserved=revision;reservation=request;request.capability_revision_token=revision.capability_revision_token;
  // A saved integration grant must never turn this replacement into execution.
  if(kind==='integration')delete request.task_grant;
  say('Submitting a replacement for a fresh exact approval.');
 }
 const selected=request.mode==='approval_decision'&&pending&&request.approval_id===pending.id&&request.approval_fingerprint===pending.fingerprint&&request.session===pending.session?{...pending}:null;
 tickets.set(request,{epoch,session:conversation,kind,selected,reserved,decision:request.approval_decision,feedback:request.approval_feedback});
 return request;
}
function accepted(request,jobId){
 const ticket=tickets.get(request);tickets.delete(request);
 if(!ticket||ticket.epoch!==epoch||ticket.session!==currentSession()||typeof jobId!=='string'||!jobId)return;
 if(ticket.reserved&&reservation===request&&revision===ticket.reserved){revision=null;reservation=null;say('Replacement requested. It still needs its own exact approval.');}
 // Only this browser's accepted request can supply a live revision token.
 inflight=ticket.kind||ticket.selected?{...ticket,jobId}:null;
}
function failed(request,error){
 const ticket=tickets.get(request);tickets.delete(request);
 if(!ticket||ticket.epoch!==epoch||ticket.session!==currentSession()||reservation!==request||revision!==ticket.reserved)return;
 reservation=null;
 if(error?.status===400)say('Preparation was rejected. Correct the inputs and explicitly retry; the link to the requested changes is retained.');
 else{revision=null;say('Submission acceptance is uncertain. Refresh the current proposal before preparing again; revision context was cleared.');}
}
submit.onclick=async()=>{
 if(!ready()||!pending||sending)return;
 const text=feedback.value.trim();if(!text||text.length>2000||text.includes('\0')){say('Describe requested changes in 1–2000 characters.');return;}
 const selected=pending,generation=epoch;sending=true;controls();
 try{const accepted=await submitAction({mode:'approval_decision',approval_decision:'request_changes',approval_id:selected.id,approval_fingerprint:selected.fingerprint,session:selected.session,approval_feedback:text});if(!accepted&&generation===epoch)say('Request did not start. Refresh the pending proposal before retrying.');}
 catch{if(generation===epoch)say('Request did not start. Refresh the pending proposal before retrying.');}
 finally{if(generation===epoch){sending=false;controls();}}
};
refresh.onclick=load;box.addEventListener('toggle',()=>{if(box.open)void load();else{readVersion++;reading=false;controls();}});
window.addEventListener('prometheus-result',event=>{
 const result=event.detail||{},data=result.details||{},ticket=inflight;
 if(state.closed||result.origin!=='local'||!ticket||result.job_id!==ticket.jobId||ticket.epoch!==epoch||ticket.session!==currentSession())return;
 inflight=null;
 if(result.error){pending=null;revision=null;reservation=null;render();say('The request failed. Refresh the pending proposal before continuing.');return;}
 if(data.approval)review(data.approval);
 if(ticket.selected&&data.approval_resolved===ticket.selected.id){pending=null;feedback.value='';render();say('Decision recorded. No replacement was prepared or executed.');}
 const context=data.capability_revision;
 if(!context)return;
 const selected=ticket.selected;
 if(!selected||ticket.decision!=='request_changes'||data.approval_resolved!==selected.id||context.kind!==selected.kind||context.origin_approval!==selected.id||context.session!==selected.session||context.session!==currentSession()||context.feedback!==ticket.feedback||!identity(context.capability_revision_token,32))return;
 revision={kind:context.kind,session:context.session,capability_revision_token:context.capability_revision_token};
 say('Changes requested for '+labels[context.kind]+'. Update the original controls and explicitly prepare a replacement. This tab will link it to review '+selected.id.slice(0,8)+'. A fresh approval is required.');
});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};if(state.closed||!state.online)clear('Reconnect and refresh the pending proposal.');else controls();});
window.addEventListener('prometheus-session-changed',()=>clear('Conversation changed. Refresh its pending proposal.'));
window.addEventListener('prometheus-auth-lost',()=>{state={busy:false,online:false,closed:true};clear('Unlock Prometheus to review a capability.');});
window.addEventListener('prometheus-restart',()=>clear('Prometheus restarted. Refresh the pending proposal; revision context was cleared.'));
document.addEventListener('visibilitychange',()=>{if(document.hidden){readVersion++;reading=false;}controls();});
window.PrometheusCapabilityRevisions={withContext,accepted,failed};
controls();
})();
