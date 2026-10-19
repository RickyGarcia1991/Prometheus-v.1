(function(){
'use strict';
const el=id=>document.getElementById(id),box=el('evidenceFollowups');
const hex=(x,n=64)=>typeof x==='string'&&new RegExp('^[a-f0-9]{'+n+'}$').test(x);
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const fields=(x,keys)=>object(x)&&Object.keys(x).length===keys.length&&keys.every(k=>Object.hasOwn(x,k));
const same=(a,b)=>a===b||Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((x,i)=>same(x,b[i]))||object(a)&&object(b)&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));
const reason=x=>typeof x==='string'&&!!x.trim()&&[...x].length<=2000&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(x)&&!/[\uD800-\uDFFF]/u.test(x);
const operations=['python-syntax-check','python-ruff-check-v1'];
const placements=['available-headroom-v1','remote-lightweight-v1'];
const contextKeys=['interpretation_id','interpretation_fingerprint','state_sha256','snapshot_sha256','fact_sha256','reason'];
const frozenKeys=['schema_version',...contextKeys,'project','interpretation','interpretation_review_id','interpretation_review_fingerprint','source_kind','source_ref','conflict_targets','evidence_snapshot'];
const paragraph=(parent,text)=>{const p=document.createElement('p');p.textContent=String(text);parent.append(p);};
const toolValid=t=>fields(t,['sha256','version'])&&hex(t.sha256)&&typeof t.version==='string'&&/^[0-9]{1,3}\.[0-9]{1,4}\.[0-9]{1,4}$/.test(t.version);
const factValid=(v,p)=>fields(v,['fact_sha256','status','checks','rollback','evidence_sha256','observation_count','witnesses','witnesses_truncated'])&&hex(v.fact_sha256)&&Number.isSafeInteger(v.observation_count)&&v.observation_count>0&&Array.isArray(v.witnesses)&&v.witnesses.length===Math.min(4,v.observation_count)&&v.witnesses_truncated===(v.observation_count>4)&&v.witnesses.every(w=>fields(w,['id','fingerprint','event_id'])&&hex(w.id,32)&&hex(w.fingerprint)&&hex(w.event_id,32))&&new Set(v.witnesses.map(w=>w.id+':'+w.fingerprint+':'+w.event_id)).size===v.witnesses.length&&window.PrometheusInterpretationLifecycle?.validatePayload({...p,fact_sha256:v.fact_sha256,selected_fact:{status:v.status,checks:v.checks,rollback:v.rollback,evidence_sha256:v.evidence_sha256}});
function contextValid(x){return object(x)&&hex(x.interpretation_id,32)&&['interpretation_fingerprint','state_sha256','snapshot_sha256','fact_sha256'].every(k=>hex(x[k]))&&reason(x.reason);}
function targetValid(x){return fields(x,['account_id','device_id','device_name','worker_digest','configuration_digest','online'])&&x.online===true&&['account_id','device_id','device_name'].every(k=>typeof x[k]==='string'&&x[k].length>0&&x[k].length<=256)&&hex(x.worker_digest)&&hex(x.configuration_digest);}
function sourceValid(f){return fields(f,['path','content'])&&typeof f.path==='string'&&f.path.length>0&&f.path.length<=1000&&!f.path.startsWith('/')&&!f.path.includes('\\')&&!f.path.includes(':')&&!f.path.split('/').some(p=>['.','..',''].includes(p))&&f.path.endsWith('.py')&&typeof f.content==='string'&&f.content.length<=256000;}
function frozenValid(f){
 if(!fields(f,frozenKeys)||f.schema_version!==1||!contextValid(f)||typeof f.project!=='string'||!f.project.length||f.project.length>500||!hex(f.interpretation_review_id,32)||!hex(f.interpretation_review_fingerprint)||!['worker','project','application'].includes(f.source_kind)||!hex(f.source_ref)||!window.PrometheusInterpretationLifecycle?.validatePayload(f.interpretation))return false;
 const p=f.interpretation,c=f.evidence_snapshot;
 return p.source===f.source_kind&&p.reference===f.source_ref&&p.snapshot_sha256===f.snapshot_sha256&&p.fact_sha256===f.fact_sha256&&Array.isArray(f.conflict_targets)&&f.conflict_targets.length===1&&fields(f.conflict_targets[0],['id','fingerprint'])&&f.conflict_targets[0].id===p.target_id&&f.conflict_targets[0].fingerprint===p.target_fingerprint&&object(c)&&c.schema_version===1&&c.read_only===true&&c.state==='unresolved'&&c.freshness==='historical'&&c.requires_new_review===true&&c.source===p.source&&c.reference===p.reference&&c.snapshot_sha256===f.snapshot_sha256&&Array.isArray(c.variants)&&c.variants.length>=2&&c.variants.length<=8&&c.variants.every(v=>factValid(v,p))&&new Set(c.variants.map(v=>v.fact_sha256)).size===c.variants.length&&c.variants.some(v=>v.fact_sha256===f.fact_sha256&&same({status:v.status,checks:v.checks,rollback:v.rollback,evidence_sha256:v.evidence_sha256},p.selected_fact));
}
function reviewValid(row){
 if(!object(row)||row.kind!=='evidence_followup'||!hex(row.id,32)||!hex(row.fingerprint)||!hex(row.session,32)||row.session!==current()||!hex(row.policy_revision)||!fields(row.payload,['request','target','placement','followup'])||!object(row.review))return false;
 const p=row.payload,r=row.review,q=p.request;
 if(!frozenValid(p.followup)||!same(p.followup,r.followup)||!targetValid(p.target)||!same(r.destination,p.target)||!object(q)||!hex(q.job_id,32)||!hex(q.request_digest)||!operations.includes(q.operation)||r.operation!==q.operation||r.request_digest!==q.request_digest||q.device_id!==p.target.device_id||q.worker_digest!==p.target.worker_digest||q.timeout_seconds!==10||!Array.isArray(q.files)||q.files.length<1||q.files.length>8||!q.files.every(sourceValid)||new Set(q.files.map(f=>f.path)).size!==q.files.length||!same(p.placement,r.placement)||!placements.includes(p.placement?.strategy)||p.placement.selected!==p.target.device_id)return false;
 const lint=q.operation==='python-ruff-check-v1',keys=['schema_version','job_id','device_id','worker_digest','operation','timeout_seconds','files','request_digest'];
 if(!fields(q,lint?[...keys,'tool','profile']:keys)||q.schema_version!==(lint?2:1))return false;
 if(lint&&(!toolValid(q.tool)||!same(q.tool,r.tool)||q.profile!=='ruff-python-v1'||r.profile!==q.profile||p.target.account_id==='local'))return false;
 if(!Array.isArray(r.files)||r.files.length!==q.files.length||!r.files.every((f,i)=>fields(f,['path','content','sha256','utf8_bytes'])&&f.path===q.files[i].path&&f.content===q.files[i].content&&hex(f.sha256)&&Number.isInteger(f.utf8_bytes)&&f.utf8_bytes>=0&&f.utf8_bytes<=512000))return false;
 return ['action','reason','benefit','effects','duration'].every(k=>typeof r[k]==='string'&&r[k].length>0&&r[k].length<=4000)&&object(r.resource_admission);
}
const provenanceKeys=[...contextKeys,'schema_version','source_kind','source_ref','interpretation_review_id','interpretation_review_fingerprint','conflict_targets','request_digest','project_sha256'];
const publicKeys=['job_id','state','destination','device_id','approved_at','expires_at','next_attempt_at','reasons','result','files','placement','operation','approval_id','fingerprint','decision','provenance','worker_digest','tool','profile','queued'];
const finite=x=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&x<=253402300799;
function provenanceValid(p){return fields(p,provenanceKeys)&&p.schema_version===1&&contextValid(p)&&['worker','project','application'].includes(p.source_kind)&&hex(p.source_ref)&&hex(p.interpretation_review_id,32)&&hex(p.interpretation_review_fingerprint)&&hex(p.request_digest)&&hex(p.project_sha256)&&Array.isArray(p.conflict_targets)&&p.conflict_targets.length===1&&p.conflict_targets.every(t=>fields(t,['id','fingerprint'])&&hex(t.id,32)&&hex(t.fingerprint));}
function resultValid(j){
 const result=j.result;if(result===null)return !['completed','failed'].includes(j.state);
 if(!fields(result,['state','receipt','evidence'])||result.state!==j.state)return false;
 if(j.state==='unconfirmed')return result.receipt===null&&result.evidence===null;
 if(!['completed','failed'].includes(j.state))return false;
 const r=result.receipt,e=result.evidence,lint=j.operation==='python-ruff-check-v1';
 const keys=['schema_version','job_id','device_id','worker_digest','request_digest','state','checks'];
 if(!fields(r,lint?[...keys,'operation','tool','profile','status','diagnostics','reason','source_unchanged']:keys)||r.schema_version!==(lint?2:1)||r.job_id!==j.job_id||r.device_id!==j.device_id||r.worker_digest!==j.worker_digest||r.request_digest!==j.provenance.request_digest||r.state!==j.state||!Array.isArray(r.checks)||r.checks.length!==j.files.length||!r.checks.every(c=>fields(c,['path','passed'])&&j.files.includes(c.path)&&typeof c.passed==='boolean')||new Set(r.checks.map(c=>c.path)).size!==j.files.length||!object(e)||e.supplemental!==true||e.authorized!==false||e.request_digest!==j.provenance.request_digest||!Array.isArray(e.reasons)||e.reasons.some(s=>typeof s!=='string'))return false;
 let expected=r.state==='completed'&&r.checks.every(c=>c.passed)?'passed':'failed';
 if(lint){
  if(r.operation!==j.operation||!same(r.tool,j.tool)||r.profile!==j.profile||typeof r.source_unchanged!=='boolean'||typeof r.reason!=='string'||r.reason.length>160||!['passed','findings','tool_failed','unavailable'].includes(r.status)||e.lint_status!==r.status||!Array.isArray(r.diagnostics)||r.diagnostics.length>16||!r.checks.every((c,i)=>c.path===j.files[i]))return false;
  const affected=new Set();
  for(const d of r.diagnostics){if(!fields(d,['path','code','message','line','column','end_line','end_column'])||!j.files.includes(d.path)||typeof d.code!=='string'||!/^(F[0-9]{3}|E9[0-9]{2}|invalid-syntax)$/.test(d.code)||typeof d.message!=='string'||!d.message.length||d.message.length>160||!['line','column','end_line','end_column'].every(k=>Number.isInteger(d[k])&&d[k]>0&&d[k]<=256001)||d.end_line<d.line||d.end_line===d.line&&d.end_column<d.column)return false;affected.add(d.path);}
  if(['passed','findings'].includes(r.status)){if(r.state!=='completed'||r.source_unchanged!==true||r.reason||!!r.diagnostics.length!==(r.status==='findings')||r.checks.some(c=>c.passed===affected.has(c.path)))return false;expected=r.status==='passed'?'passed':'failed';}
  else if(r.state!=='failed'||r.diagnostics.length||r.checks.some(c=>c.passed)||!r.reason)return false;
 }
 return e.status===expected;
}
function jobValid(j){
 if(!fields(j,publicKeys)||!hex(j.job_id,32)||!hex(j.approval_id,32)||!hex(j.fingerprint)||!hex(j.worker_digest)||!provenanceValid(j.provenance)||!['rejected','waiting','cancelled','expired','unconfirmed','completed','failed'].includes(j.state)||!['approve','reject','unrecorded'].includes(j.decision)||typeof j.queued!=='boolean'||!operations.includes(j.operation)||typeof j.destination!=='string'||!j.destination.length||typeof j.device_id!=='string'||!j.device_id.length||!Array.isArray(j.files)||j.files.length<1||j.files.length>8||j.files.some(p=>typeof p!=='string'||!p.length||p.length>240)||new Set(j.files).size!==j.files.length||!Array.isArray(j.reasons)||j.reasons.length>16||j.reasons.some(r=>typeof r!=='string'||r.length>512)||!object(j.placement)||!placements.includes(j.placement.strategy)||j.placement.selected!==j.device_id||!finite(j.expires_at)||j.next_attempt_at!==null&&!finite(j.next_attempt_at)||j.approved_at!==null&&(!finite(j.approved_at)||j.expires_at-j.approved_at!==3600))return false;
 if(j.queued&&j.decision!=='approve'||j.decision==='approve'&&j.approved_at===null||j.decision!=='approve'&&j.approved_at!==null||!j.queued&&j.state!==(j.decision==='reject'?'rejected':'unconfirmed'))return false;
 if(j.operation==='python-ruff-check-v1'?!toolValid(j.tool)||j.profile!=='ruff-python-v1':j.tool!==null||j.profile!==null)return false;
 return resultValid(j);
}
function related(j,row){const p=j.provenance;return [[j.approval_id,j.fingerprint],[p.interpretation_id,p.interpretation_fingerprint],[p.interpretation_review_id,p.interpretation_review_fingerprint],...p.conflict_targets.map(t=>[t.id,t.fingerprint])].some(([id,fp])=>row.id===id&&row.fingerprint===fp);}
function renderHistory(parent,row){
 const p=row.evidence_followups;if(p===undefined)return;
 if(!fields(p,['schema_version','read_only','freshness','items','total','truncated'])||p.schema_version!==1||p.read_only!==true||p.freshness!=='historical'||!Array.isArray(p.items)||!Number.isInteger(p.total)||p.total<0||p.total>100||p.items.length!==Math.min(16,p.total)||p.truncated!==(p.total>16)||!p.items.every(j=>jobValid(j)&&related(j,row))||new Set(p.items.map(j=>j.job_id)).size!==p.items.length){
  paragraph(parent,'Follow-up history unavailable. Original evidence remains visible.');return;
 }
 for(const item of p.items){
  const provenance=item.provenance;
  paragraph(parent,'Follow-up '+item.job_id+' · '+String(item.state||item.status||'unconfirmed')+' · interpretation '+provenance.interpretation_id+'.');
  paragraph(parent,'Reason: '+provenance.reason+'. Original checks and conflicts are unchanged.');
  if(item.result?.evidence)paragraph(parent,'Supplemental check evidence: '+item.result.evidence.status+'.');
 }
 if(p.truncated)paragraph(parent,'Additional follow-up records remain stored.');
}
window.PrometheusEvidenceFollowups=Object.freeze({renderHistory});
if(!box)return;
let state={online:false,busy:false,closed:false},epoch=0,reading=false,sending=false,active=null,pending=null,other=false,items=[],jobs=[],buttons=[];
const current=()=>typeof session==='string'?session:null;
const ready=()=>state.online&&!state.busy&&!state.closed&&!document.hidden&&box.open;
const say=x=>{el('followupStatus').textContent=x;};
const selected=()=>items.find(x=>x.interpretation_id===el('followupInterpretation').value);
const workspace=()=>window.PrometheusWorkspace?.context();
function controls(){
 const locked=!ready()||reading||sending,x=selected(),w=workspace(),files=el('followupFiles').value;
 el('followupRefresh').disabled=locked;
 for(const id of ['followupInterpretation','followupFiles','followupOperation','followupPlacement','followupReason'])el(id).disabled=locked||!!pending||other;
 el('followupPrepare').disabled=locked||!!pending||other||!x||x.state!=='active'||x.currency!=='matching_inputs'||!w?.project||w.dirty||w.stale||!reason(el('followupReason').value.trim())||!files||files.length>4000||files.split(/\r?\n/).length>8||files.split(/\r?\n/).some(x=>!x.trim())||!operations.includes(el('followupOperation').value)||!placements.includes(el('followupPlacement').value);
 for(const id of ['followupApprove','followupReject'])el(id).disabled=locked||!pending;
 for(const b of buttons)b.disabled=locked||!!pending||other;
}
function review(row){
 pending=reviewValid(row)?row:null;other=!!row&&!pending;
 el('followupReview').replaceChildren();el('followupReview').hidden=!pending;el('followupDecisions').hidden=!pending;
 if(pending){const pre=document.createElement('pre');pre.className='code-output';pre.textContent=JSON.stringify(pending.review,null,2);el('followupReview').append(pre);say('Review the exact evidence, source and destination. This approval permits only the shown check and bounded waiting.');}
 else if(other)say(row.kind==='evidence_followup'?'Exact follow-up review unavailable. Refresh before deciding.':'Another proposal is pending. Finish its review first.');
 controls();
}
function details(){
 const x=selected(),p=el('followupInterpretationDetails');p.replaceChildren();
 if(x){paragraph(p,x.effective_payload.reason);paragraph(p,'Recorded '+x.source+' observation: '+x.effective_payload.selected_fact.status+'.');
  if(x.state!=='active'||x.currency!=='matching_inputs')paragraph(p,'This interpretation is withdrawn or stale. Refresh or complete a new interpretation review before requesting work.');
  if(x.completion_unconfirmed)paragraph(p,'An earlier change has unconfirmed completion. This check binds the exact current effective interpretation and state, without completing that earlier attempt.');
  if(x.interpretation_conflict)paragraph(p,'Competing interpretations remain visible. This check does not select a winner.');
  if(x.history_conflict)paragraph(p,'Conflicting original decisions remain in history.');}
 controls();
}
function button(parent,label,mode,id){const b=document.createElement('button');b.textContent=label;b.onclick=()=>{if(!b.disabled)return send({mode,followup_job_id:id});};parent.append(b);buttons.push(b);}
function renderJobs(){
 const parent=el('followupJobs');parent.replaceChildren();buttons=[];
 if(!jobs.length)paragraph(parent,'No saved follow-up attempts.');
 for(const j of jobs){
  if(!jobValid(j))throw Error('Invalid follow-up history');
  const card=document.createElement('section');card.className='workspace-card';paragraph(card,String(j.destination||'Reviewed destination')+' · '+j.state+' · '+j.operation);paragraph(card,'Follow-up '+j.job_id+' · files '+j.files.join(', '));
  if(j.provenance&&contextValid(j.provenance))paragraph(card,'Interpretation '+j.provenance.interpretation_id+' · '+j.provenance.reason);
  for(const r of Array.isArray(j.reasons)?j.reasons:[])paragraph(card,r);
  if(j.state==='waiting')button(card,'Cancel waiting follow-up','evidence_followup_cancel',j.job_id);
  if(j.state==='unconfirmed'){paragraph(card,j.queued?'A claim has no confirmed result. Recovery queries only that original job and never runs it again.':'This attempt was not durably queued. Its decision grants no restart authority.');if(j.queued)button(card,'Recover original follow-up result','evidence_followup_recover',j.job_id);}
  if(j.result){const pre=document.createElement('pre');pre.textContent=JSON.stringify(j.result,null,2);card.append(pre);}
  paragraph(card,'Supplemental check only. Original evidence and failed results are unchanged.');parent.append(card);
 }
 controls();
}
function clear(message){epoch++;reading=false;active=null;items=[];jobs=[];pending=null;other=false;el('followupInterpretation').replaceChildren();el('followupInterpretationDetails').replaceChildren();el('followupJobs').replaceChildren();buttons=[];review(null);say(message);}
async function load(){
 if(!ready()||reading||sending)return;
 const mark=++epoch,conversation=current();reading=true;controls();
 try{
  const [h,queue,live]=await Promise.all([api('/api/decisions/history'),api('/api/evidence/followups'),api('/api/approvals/pending')]);
  if(mark!==epoch||!ready()||conversation!==current())return;
  if(h?.schema_version!==1||h.read_only!==true||!Array.isArray(h.items)||h.items.length>50||queue?.schema_version!==1||queue.read_only!==true||!Array.isArray(queue.jobs)||queue.jobs.length>100)throw Error('Invalid history');
  const unique=new Map();
  for(const row of h.items){if(row.interpretation_lifecycle===undefined)continue;if(!window.PrometheusInterpretationLifecycle?.validateProjection(row.interpretation_lifecycle,row))throw Error('Invalid interpretation history');for(const x of row.interpretation_lifecycle.items){const old=unique.get(x.interpretation_id);if(old&&Object.keys(x).filter(k=>k!=='changes').some(k=>!same(old[k],x[k])))throw Error('Inconsistent history');unique.set(x.interpretation_id,x);}}
  items=[...unique.values()];jobs=queue.jobs;const select=el('followupInterpretation');select.replaceChildren();select.add(new Option('Choose an active interpretation…',''));
  for(const x of items)select.add(new Option(x.source+' · '+x.state+' · '+x.interpretation_id.slice(0,12),x.interpretation_id));
  details();renderJobs();review(live.approval);
  if(!pending&&!other)say(items.length+' interpretation histories and '+jobs.length+' follow-up attempts. Open a project and choose its saved files for a new review.');
 }catch(error){if(mark===epoch)clear('Follow-up history unavailable. Original evidence remains saved. Refresh before acting.');}
 finally{if(mark===epoch){reading=false;controls();}}
}
async function send(body){
 if(!ready()||sending||reading)return false;
 const mark=epoch;sending=true;active={epoch:mark,session:current(),mode:body.mode};controls();
 try{const ok=await submitAction({...body});if(!ok&&mark===epoch){active=null;say('Request did not start. Refresh before trying again.');}return ok;}
 catch(error){if(mark===epoch){active=null;say(error.message||'Follow-up request failed.');}return false;}
 finally{sending=false;controls();}
}
el('followupRefresh').onclick=load;el('followupInterpretation').onchange=details;
for(const id of ['followupFiles','followupReason'])el(id).oninput=controls;
for(const id of ['followupOperation','followupPlacement'])el(id).onchange=controls;
el('followupPrepare').onclick=()=>{
 controls();if(el('followupPrepare').disabled)return;
 const x=selected(),w=workspace();return send({mode:'evidence_followup_prepare',...(current()?{session:current()}:{}),project:w.project,remote_files:el('followupFiles').value,
  followup_interpretation_id:x.interpretation_id,followup_interpretation_fingerprint:x.interpretation_fingerprint,followup_state_sha256:x.state_sha256,
  followup_snapshot_sha256:x.effective_payload.snapshot_sha256,followup_fact_sha256:x.effective_payload.fact_sha256,followup_reason:el('followupReason').value,
  followup_operation:el('followupOperation').value,followup_placement:el('followupPlacement').value});
};
function decide(decision){if(pending&&ready()&&!reading&&!sending)return send({mode:'approval_decision',session:pending.session,approval_id:pending.id,approval_fingerprint:pending.fingerprint,approval_decision:decision});}
el('followupApprove').onclick=()=>decide('approve');el('followupReject').onclick=()=>decide('reject');
box.addEventListener('toggle',()=>{if(box.open)void load();else clear('Open to read follow-up checks.');});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};if(!state.online||state.closed)clear('Unlock Prometheus to use follow-up checks.');else if(state.busy){epoch++;reading=false;if(active)active.epoch=epoch;}controls();});
window.addEventListener('prometheus-result',event=>{
 const result=event.detail||{},data=result.details||{};
 if(!active||active.epoch!==epoch||result.origin!=='local'||state.closed||!state.online||document.hidden||!box.open||active.session&&active.session!==current()||result.action!==active.mode)return;
 active=null;if(result.error){review(null);say(result.error+' Refresh before acting.');return;}
 if(data.approval)review(data.approval);
 if(data.approval_resolved||Object.hasOwn(data,'evidence_followup')){review(null);items=[];el('followupInterpretation').replaceChildren();say(result.reply||'Follow-up updated. Refresh to read its saved result.');controls();}
});
window.addEventListener('prometheus-auth-lost',()=>{state.online=false;clear('Unlock Prometheus to use follow-up checks.');});
window.addEventListener('prometheus-session-changed',()=>{if(active?.mode==='evidence_followup_prepare'&&active.session===null){active.session=current();return;}clear('Conversation changed. Refresh the exact follow-up review.');});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clear('Review paused while hidden. Refresh when ready.');controls();});
controls();
})();
