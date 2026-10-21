(function(){
'use strict';
const el=id=>document.getElementById(id),box=el('interpretationLifecycle');
const hex=(x,n)=>typeof x==='string'&&new RegExp('^[a-f0-9]{'+n+'}$').test(x);
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const fields=(x,keys)=>object(x)&&Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const same=(a,b)=>a===b||object(a)&&object(b)&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));
const reason=x=>typeof x==='string'&&!!x.trim()&&x===x.trim()&&[...x].length<=2000&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(x)&&!/[\uD800-\uDFFF]/u.test(x);
const countKeys=['planned','executed','passed','failed','cancelled','timed_out'];
const payloadKeys=['target_id','target_fingerprint','source','reference','snapshot_sha256','fact_sha256','selected_fact','reason'];
const changeKeys=['interpretation_id','interpretation_fingerprint','expected_state_sha256','operation','reason','replacement'];
const itemKeys=['interpretation_id','interpretation_fingerprint','target_id','target_fingerprint','source','reference','state_sha256','state','effective_payload','effective_review_id','effective_review_fingerprint','effective_change','currency','interpretation_conflict','history_conflict','total_changes','changes_truncated','changes','completion_unconfirmed'];
const eventKeys=['approval_id','fingerprint','operation','reason','decision','recording_state','created_at'];
const reviewSummary='Change a recorded owner interpretation.';
const reviewEffect='Original interpretations, observations and failed checks remain in history. No execution or retry is authorized.';
function counts(x){return fields(x,countKeys)&&countKeys.every(k=>Number.isInteger(x[k])&&x[k]>=0&&x[k]<=64)&&x.executed<=x.planned&&x.passed+x.failed+x.cancelled+x.timed_out===x.executed;}
function factValid(f,source){
 if(!fields(f,['status','checks','rollback','evidence_sha256'])||!hex(f.evidence_sha256,64)||f.checks!==null&&!counts(f.checks))return false;
 if(source==='application')return ['active','failed','rolled_back','recovery_required'].includes(f.status)&&f.checks===null&&f.rollback===null;
 if(source==='project')return ['passed','failed','cancelled','unchanged','applied_without_checks'].includes(f.status)&&['not_attempted','restored','needs_attention','unknown'].includes(f.rollback)&&(f.status!=='passed'||f.rollback==='not_attempted'&&f.checks!==null&&f.checks.planned>0&&f.checks.passed===f.checks.planned);
 if(source!=='worker'||f.rollback!==null)return false;
 if(['tool_failed','cancelled','expired'].includes(f.status))return f.checks===null;
 return ['checks_passed','checks_failed'].includes(f.status)&&f.checks!==null&&f.checks.planned>0&&(f.status==='checks_passed'?f.checks.passed===f.checks.planned:f.checks.failed>0);
}
function payloadValid(x){return fields(x,payloadKeys)&&hex(x.target_id,32)&&['target_fingerprint','reference','snapshot_sha256','fact_sha256'].every(k=>hex(x[k],64))&&factValid(x.selected_fact,x.source)&&reason(x.reason);}
function eventValid(x){return fields(x,eventKeys)&&hex(x.approval_id,32)&&hex(x.fingerprint,64)&&['revise','withdraw'].includes(x.operation)&&reason(x.reason)&&['approve','reject','unrecorded'].includes(x.decision)&&['recorded','unrecorded'].includes(x.recording_state)&&(x.decision!=='unrecorded'||x.recording_state==='unrecorded')&&Number.isFinite(x.created_at)&&x.created_at>=0&&x.created_at<=253402300799;}
function itemValid(x){
 if(!fields(x,itemKeys)||!['interpretation_id','target_id','effective_review_id'].every(k=>hex(x[k],32))||!['interpretation_fingerprint','target_fingerprint','reference','state_sha256','effective_review_fingerprint'].every(k=>hex(x[k],64))||!['active','withdrawn'].includes(x.state)||!['matching_inputs','stale'].includes(x.currency)||!['interpretation_conflict','history_conflict','completion_unconfirmed','changes_truncated'].every(k=>typeof x[k]==='boolean')||!payloadValid(x.effective_payload)||!['target_id','target_fingerprint','source','reference'].every(k=>x[k]===x.effective_payload[k]))return false;
 if(!(Number.isInteger(x.total_changes)&&x.total_changes>=0&&x.total_changes<=4096&&Array.isArray(x.changes)&&x.changes.length===Math.min(16,x.total_changes)&&x.changes_truncated===(x.total_changes>16)&&x.changes.every(eventValid)&&new Set(x.changes.map(v=>v.approval_id)).size===x.changes.length))return false;
 const c=x.effective_change;
 if(c===null){if(x.state!=='active'||x.effective_review_id!==x.interpretation_id||x.effective_review_fingerprint!==x.interpretation_fingerprint)return false;}
 else if(!eventValid(c)||c.decision!=='approve'||c.recording_state!=='recorded'||c.approval_id!==x.effective_review_id||c.fingerprint!==x.effective_review_fingerprint||c.operation!==(x.state==='withdrawn'?'withdraw':'revise')||c.operation==='revise'&&c.reason!==x.effective_payload.reason||c.approval_id===x.interpretation_id||x.total_changes===0)return false;
 const shown=c&&x.changes.find(v=>v.approval_id===c.approval_id);if(shown&&!same(shown,c))return false;
 if(!x.changes_truncated){const effective=x.changes.find(v=>v.decision==='approve'&&v.recording_state==='recorded');if(effective?!c||!same(c,effective):c!==null)return false;if(x.completion_unconfirmed!==x.changes.some(v=>v.recording_state==='unrecorded'))return false;}
 return true;
}
function projectionValid(p,row){
 if(!fields(p,['schema_version','read_only','freshness','total','truncated','items'])||p.schema_version!==1||p.read_only!==true||p.freshness!=='historical'||!Number.isInteger(p.total)||p.total<0||p.total>4096||!Array.isArray(p.items)||p.items.length!==Math.min(16,p.total)||p.truncated!==(p.total>16)||!p.items.every(itemValid)||new Set(p.items.map(x=>x.interpretation_id)).size!==p.items.length)return false;
 return p.items.every(x=>x.target_id===row.id&&x.target_fingerprint===row.fingerprint||x.interpretation_id===row.id&&x.interpretation_fingerprint===row.fingerprint||x.changes.some(c=>c.approval_id===row.id&&c.fingerprint===row.fingerprint));
}
const paragraph=(parent,text)=>{const p=document.createElement('p');p.textContent=text;parent.append(p);};
function renderItem(parent,x){
 paragraph(parent,'Interpretation '+x.interpretation_id+' · '+(x.state==='withdrawn'?'Withdrawn; original interpretation retained.':'Effective interpretation: '+x.effective_payload.reason));
 paragraph(parent,(x.currency==='stale'?'Stale: collected evidence has changed.':'Matches collected history inputs; not a current health check.')+' Selected observation '+x.effective_payload.fact_sha256+'.');
 if(x.interpretation_conflict)paragraph(parent,'Competing effective interpretations select different observations. No automatic winner is chosen.');
 if(x.history_conflict)paragraph(parent,'Conflicting original approvals remain in historical evidence.');
 if(x.completion_unconfirmed)paragraph(parent,'Earlier change attempt completion is unconfirmed. Only completed approvals determine the effective interpretation.');
 for(const c of x.changes)paragraph(parent,c.operation+' · '+c.decision+' · '+(c.recording_state==='unrecorded'?'completion unconfirmed':'recorded')+' · '+c.reason+' · review '+c.approval_id+'.');
 if(x.changes_truncated)paragraph(parent,'Showing '+x.changes.length+' of '+x.total_changes+' changes, including this selected review when applicable; earlier attempts remain stored.');
}
function renderHistory(parent,row){
 const p=row.interpretation_lifecycle;if(p===undefined)return;
 if(!projectionValid(p,row)){paragraph(parent,'Interpretation lifecycle unavailable. Original evidence remains visible.');return;}
 if(!p.items.length)return;
 paragraph(parent,'Interpretation lifecycle: original explanations and all attempts remain in history.');
 for(const x of p.items)renderItem(parent,x);
 if(p.truncated)paragraph(parent,'Showing '+p.items.length+' of '+p.total+' interpretation histories.');
 paragraph(parent,'Interpretation changes grant no execution, repair or retry authority.');
}
window.PrometheusInterpretationLifecycle=Object.freeze({renderHistory,validateProjection:projectionValid,validatePayload:payloadValid});
if(!box)return;
let state={online:false,busy:false,closed:false},epoch=0,reading=false,sending=false,active=null,pending=null,items=[],rows=[];
const current=()=>typeof session==='string'?session:null;
const ready=()=>state.online&&!state.busy&&!state.closed&&!document.hidden;
const say=x=>{el('interpretationStatus').textContent=x;};
const selected=()=>items.find(x=>x.interpretation_id===el('interpretationTarget').value);
function conflict(x){
 const c=rows.find(r=>r.id===x?.target_id&&r.fingerprint===x?.target_fingerprint)?.evidence_conflict;
 if(!c||c.schema_version!==1||c.read_only!==true||c.state!=='unresolved'||c.freshness!=='historical'||c.requires_new_review!==true||c.source!==x.source||c.reference!==x.reference||!hex(c.snapshot_sha256,64)||!Array.isArray(c.variants)||c.variants.length<2||c.variants.length>8)return null;
 return c.variants.every(v=>hex(v.fact_sha256,64)&&factValid({status:v.status,checks:v.checks,rollback:v.rollback,evidence_sha256:v.evidence_sha256},c.source))?c:null;
}
function controls(){
 const locked=!ready()||reading||sending||!box.open,x=selected(),operation=el('interpretationOperation').value,c=conflict(x);
 el('interpretationRefresh').disabled=locked;
 for(const id of ['interpretationTarget','interpretationOperation','interpretationReason'])el(id).disabled=locked||!!pending;
 el('interpretationFact').disabled=locked||!!pending||operation!=='revise';
 el('interpretationPrepare').disabled=locked||!!pending||!x||x.state!=='active'||!['revise','withdraw'].includes(operation)||!reason(el('interpretationReason').value.trim())||operation==='revise'&&(!c||!c.variants.some(v=>v.fact_sha256===el('interpretationFact').value));
 for(const id of ['interpretationApprove','interpretationReject'])el(id).disabled=locked||!pending;
}
function reviewValid(row){
 if(!object(row)||row.kind!=='evidence_interpretation_change'||!hex(row.id,32)||!hex(row.fingerprint,64)||!hex(row.session,32)||row.session!==current()||typeof row.policy_revision!=='string'||!row.policy_revision.length||row.policy_revision.length>256||/[\u0000-\u001f]/.test(row.policy_revision))return false;
 const p=row.payload,r=row.review;
 return fields(p,changeKeys)&&fields(r,[...changeKeys,'summary','effect'])&&r.summary===reviewSummary&&r.effect===reviewEffect&&changeKeys.every(k=>same(p[k],r[k]))&&hex(p.interpretation_id,32)&&hex(p.interpretation_fingerprint,64)&&hex(p.expected_state_sha256,64)&&reason(p.reason)&&(p.operation==='withdraw'?p.replacement===null:p.operation==='revise'&&payloadValid(p.replacement)&&p.replacement.reason===p.reason);
}
function review(row){
 pending=reviewValid(row)?row:null;el('interpretationReview').replaceChildren();el('interpretationReview').hidden=!pending;el('interpretationDecisions').hidden=!pending;
 if(pending){const p=document.createElement('pre');p.textContent=JSON.stringify(pending.review,null,2);el('interpretationReview').append(p);say('Approve or deny this exact change. A newer interpretation change requires a fresh review.');}
 else if(row?.kind==='evidence_interpretation_change')say('Exact interpretation change review unavailable. Refresh before deciding.');
 controls();
}
function details(){
 const x=selected(),c=conflict(x),select=el('interpretationFact');el('interpretationDetails').replaceChildren();select.replaceChildren();select.add(new Option('Choose a current observation…',''));
 if(x)renderItem(el('interpretationDetails'),x);
 for(const v of c?.variants||[])select.add(new Option(v.status+' · '+v.fact_sha256.slice(0,12),v.fact_sha256));
 controls();
}
function clear(message){epoch++;reading=false;active=null;items=[];rows=[];pending=null;el('interpretationTarget').replaceChildren();el('interpretationFact').replaceChildren();el('interpretationDetails').replaceChildren();el('interpretationReason').value='';review(null);say(message);}
async function load(){
 if(!ready()||reading||sending||!box.open)return;
 const mark=++epoch,conversation=current();reading=true;controls();
 try{
  const h=await api('/api/decisions/history'),live=await api('/api/approvals/pending');
  if(mark!==epoch||!ready()||!box.open||conversation!==current())return;
  if(h?.schema_version!==1||h.read_only!==true||!Array.isArray(h.items)||h.items.length>50)throw Error('Invalid history');
  rows=h.items;const unique=new Map();
  for(const r of rows){if(r.interpretation_lifecycle===undefined)continue;if(!projectionValid(r.interpretation_lifecycle,r))throw Error('Invalid lifecycle history');for(const x of r.interpretation_lifecycle.items){const old=unique.get(x.interpretation_id);if(old&&!itemKeys.filter(k=>k!=='changes').every(k=>same(old[k],x[k])))throw Error('Inconsistent history');unique.set(x.interpretation_id,x);}}
  items=[...unique.values()];const select=el('interpretationTarget');select.replaceChildren();select.add(new Option('Choose an interpretation…',''));
  for(const x of items)select.add(new Option(x.source+' · '+x.state+' · '+x.interpretation_id.slice(0,12),x.interpretation_id));
  details();review(live.approval);if(!pending&&live.approval?.kind!=='evidence_interpretation_change')say(live.approval?'Another proposal is pending. Finish its review first.':items.length+' interpretation histories in the latest 50 decisions. Refresh before preparing a change.');
 }catch(error){if(mark===epoch)clear('Interpretation lifecycle unavailable. Original evidence remains in history. Refresh when ready.');}
 finally{if(mark===epoch){reading=false;controls();}}
}
async function send(body){
 if(!ready()||sending||reading)return false;
 const mark=epoch;sending=true;active={epoch:mark,session:current(),mode:body.mode};controls();
 try{const ok=await submitAction({...body});if(!ok&&mark===epoch){active=null;say('Request did not start. Refresh before trying again.');}return ok;}
 catch(error){if(mark===epoch){active=null;say(error.message||'Interpretation change failed.');}return false;}
 finally{sending=false;controls();}
}
el('interpretationRefresh').onclick=load;el('interpretationTarget').onchange=details;el('interpretationOperation').onchange=controls;el('interpretationFact').onchange=controls;el('interpretationReason').oninput=controls;
el('interpretationPrepare').onclick=()=>{
 controls();if(el('interpretationPrepare').disabled)return;
 const x=selected(),operation=el('interpretationOperation').value,c=conflict(x);
 return send({mode:'interpretation_change_prepare',...(current()?{session:current()}:{}),interpretation_id:x.interpretation_id,interpretation_fingerprint:x.interpretation_fingerprint,interpretation_state_sha256:x.state_sha256,interpretation_operation:operation,interpretation_reason:el('interpretationReason').value,...(operation==='revise'?{interpretation_snapshot_sha256:c.snapshot_sha256,interpretation_fact_sha256:el('interpretationFact').value}:{})});
};
function decide(decision){if(pending&&ready()&&box.open)return send({mode:'approval_decision',session:pending.session,approval_id:pending.id,approval_fingerprint:pending.fingerprint,approval_decision:decision});}
el('interpretationApprove').onclick=()=>decide('approve');el('interpretationReject').onclick=()=>decide('reject');
box.addEventListener('toggle',()=>{if(box.open)void load();else clear('Open to read interpretation history.');});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};if(!state.online||state.closed)clear('Unlock Prometheus to change an interpretation.');else if(state.busy){epoch++;reading=false;if(active)active.epoch=epoch;}controls();});
window.addEventListener('prometheus-result',event=>{
 const result=event.detail||{},data=result.details||{};
 if(!active||active.epoch!==epoch||result.origin!=='local'||state.closed||!state.online||document.hidden||!box.open||active.session&&active.session!==current()||result.action!==active.mode)return;
 active=null;if(result.error){review(null);say(result.error+' Refresh the live review before trying again.');return;}
 if(data.approval)review(data.approval);
 if(data.approval_resolved){review(null);items=[];rows=[];el('interpretationTarget').replaceChildren();el('interpretationFact').replaceChildren();say(result.reply||'Change recorded. Refresh history for the effective interpretation.');controls();}
});
window.addEventListener('prometheus-auth-lost',()=>{state.online=false;clear('Unlock Prometheus to change an interpretation.');});
window.addEventListener('prometheus-session-changed',()=>{if(active?.mode==='interpretation_change_prepare'&&active.session===null){active.session=current();return;}clear('Conversation changed. Refresh interpretations and the live review.');});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clear('Review paused while hidden. Refresh when ready.');controls();});
controls();
})();
