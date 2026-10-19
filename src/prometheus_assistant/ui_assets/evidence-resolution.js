(function () {
'use strict';
const el=id=>document.getElementById(id), box=el('evidenceResolution');
let state={online:false,busy:false,closed:false}, epoch=0, reading=false, sending=false, pending=null, rows=[], active=null;
const hex=(x,n)=>typeof x==='string'&&new RegExp('^[a-f0-9]{'+n+'}$').test(x);
const current=()=>typeof session==='string'?session:null;
const ready=()=>state.online&&!state.busy&&!state.closed&&!document.hidden;
const say=x=>{el('resolutionStatus').textContent=x;};
const target=()=>rows.find(x=>x.id===el('resolutionTarget').value);
function controls(){
 const locked=!ready()||reading||sending;
 el('resolutionRefresh').disabled=locked;
 for(const id of ['resolutionTarget','resolutionFact','resolutionReason'])el(id).disabled=locked||!!pending;
 el('resolutionPrepare').disabled=locked||!!pending||!target()||!el('resolutionReason').value.trim()||!hex(el('resolutionFact').value,64);
 for(const id of ['resolutionApprove','resolutionReject'])el(id).disabled=locked||!pending;
}
function review(row){
 pending=row?.kind==='evidence_resolution'&&hex(row.id,32)&&hex(row.fingerprint,64)&&row.session===current()?row:null;
 el('resolutionReview').replaceChildren();el('resolutionReview').hidden=!pending;el('resolutionDecisions').hidden=!pending;
 if(pending){const p=document.createElement('pre');p.textContent=JSON.stringify(pending.review,null,2);el('resolutionReview').append(p);say('Approve or deny this exact interpretation. Changed evidence requires a new review.');}
 controls();
}
function clear(message){epoch++;reading=false;active=null;rows=[];pending=null;el('resolutionTarget').replaceChildren();el('resolutionFact').replaceChildren();el('resolutionFacts').replaceChildren();el('resolutionReason').value='';review(null);say(message);}
function facts(){
 const row=target(),select=el('resolutionFact');select.replaceChildren();select.add(new Option('Choose an observation…',''));
 el('resolutionFacts').replaceChildren();
 for(const fact of row?.evidence_conflict?.variants||[]){select.add(new Option(fact.status+' · '+fact.fact_sha256.slice(0,12),fact.fact_sha256));const p=document.createElement('pre');p.textContent=JSON.stringify(fact,null,2);el('resolutionFacts').append(p);}
 controls();
}
function valid(row){
 const c=row?.evidence_conflict;
 return hex(row?.id,32)&&hex(row?.fingerprint,64)&&c?.schema_version===1&&c.state==='unresolved'&&c.read_only===true&&c.freshness==='historical'&&c.requires_new_review===true&&hex(c.snapshot_sha256,64)&&Array.isArray(c.variants)&&c.variants.length>=2&&c.variants.length<=8&&c.variants.every(v=>hex(v.fact_sha256,64)&&typeof v.status==='string'&&v.status.length<=40);
}
async function load(){
 if(!ready()||reading||sending||!box.open)return;
 const mark=++epoch, conversation=current();reading=true;controls();
 try{
  const history=await api('/api/decisions/history'), live=await api('/api/approvals/pending');
  if(mark!==epoch||!ready()||!box.open||conversation!==current())return;
  if(history?.schema_version!==1||history.read_only!==true||!Array.isArray(history.items)||history.items.length>50)throw Error('Invalid history');
  rows=history.items.filter(valid);const select=el('resolutionTarget');select.replaceChildren();select.add(new Option('Choose conflicting evidence…',''));
  for(const row of rows)select.add(new Option(row.evidence_conflict.source+' · '+row.id.slice(0,12),row.id));
  facts();review(live.approval);if(!pending)say(live.approval?'Another proposal is pending. Finish its review first.':rows.length+' conflicting records in the latest 50 decisions. Original results remain unchanged.');
 }catch(error){if(mark===epoch)clear('Conflict reviews unavailable. Refresh when the connection is ready.');}
 finally{if(mark===epoch){reading=false;controls();}}
}
async function send(body){
 if(!ready()||sending||reading)return false;
 const mark=epoch; sending=true;active={epoch:mark,session:current(),mode:body.mode};controls();
 try{const accepted=await submitAction({...body});if(!accepted&&mark===epoch){active=null;say('Request did not start. Refresh before trying again.');}return accepted;}
 catch(error){if(mark===epoch){active=null;say(error.message||'Interpretation request failed.');}return false;}
 finally{sending=false;controls();}
}
el('resolutionRefresh').onclick=load;
el('resolutionTarget').onchange=facts;el('resolutionFact').onchange=controls;el('resolutionReason').oninput=controls;
el('resolutionPrepare').onclick=()=>{
 const row=target(),fact=el('resolutionFact').value,reason=el('resolutionReason').value;
 if(!row||!row.evidence_conflict.variants.some(v=>v.fact_sha256===fact)||!reason.trim())return;
 return send({mode:'evidence_resolution_prepare',...(current()?{session:current()}:{}),resolution_target_id:row.id,resolution_target_fingerprint:row.fingerprint,resolution_snapshot_sha256:row.evidence_conflict.snapshot_sha256,resolution_fact_sha256:fact,resolution_reason:reason});
};
function decide(decision){if(pending)return send({mode:'approval_decision',session:pending.session,approval_id:pending.id,approval_fingerprint:pending.fingerprint,approval_decision:decision});}
el('resolutionApprove').onclick=()=>decide('approve');el('resolutionReject').onclick=()=>decide('reject');
box.addEventListener('toggle',()=>{if(box.open)void load();else clear('Open to read conflicts from the latest 50 decisions.');});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};if(!state.online||state.closed)clear('Unlock Prometheus to review evidence.');else if(state.busy){epoch++;reading=false;if(active)active.epoch=epoch;}controls();});
window.addEventListener('prometheus-result',event=>{
 const result=event.detail||{}, data=result.details||{};
 if(!active||active.epoch!==epoch||result.origin!=='local'||state.closed||!state.online||document.hidden||active.session&&active.session!==current())return;
 if(result.action!==active.mode)return;
 active=null;
 if(result.error){review(null);say(result.error+' Refresh the exact pending review before trying again.');return;}
 if(data.approval)review(data.approval);
 if(data.approval_resolved){review(null);say(result.reply||'Interpretation decision saved. Refresh owner history to inspect the record.');}
});
window.addEventListener('prometheus-auth-lost',()=>{state.online=false;clear('Unlock Prometheus to review evidence.');});
window.addEventListener('prometheus-session-changed',()=>{
 if(active?.mode==='evidence_resolution_prepare'&&active.session===null){active.session=current();return;}
 clear('Conversation changed. Refresh conflicts and the live pending review.');
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clear('Review paused while hidden. Refresh when ready.');controls();});
controls();
})();
