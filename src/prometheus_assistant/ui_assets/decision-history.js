(function(){
'use strict';
const el=id=>document.getElementById(id),box=el('decisionHistory'),list=el('decisionHistoryList'),status=el('decisionHistoryStatus'),refresh=el('decisionHistoryRefresh');
if(!box||!list||!status||!refresh)return;
let state={online:false,busy:false,closed:false},generation=0,reading=false;
const ready=()=>state.online&&!state.busy&&!state.closed&&!document.hidden;
const labels={runtime_config:'Runtime settings',project_edit:'Project change',project_task:'Project task',idle_config:'Research settings',self_improve:'Application improvement',scheduled_syntax:'Scheduled check',remote_syntax:'Remote check',integration:'Integration',integration_access:'Integration access'};
const decisions={approve:'Approved',reject:'Denied',request_changes:'Changes requested'};
const phases={started:['started'],result:['passed','failed','cancelled','unchanged','applied_without_checks','unknown'],undo_started:['started'],undo_result:['restored','restore_failed','unknown']};
const statuses={started:'Execution started',passed:'Native checks passed',failed:'Execution failed',cancelled:'Execution cancelled',unchanged:'No change recorded',applied_without_checks:'Applied without checks; behavior is unverified',unknown:'Outcome unknown',restored:'Manual restoration completed',restore_failed:'Manual restoration failed'};
const rollbacks={not_attempted:'not attempted',restored:'original content restored',needs_attention:'needs attention',unknown:'unknown'};
function events(row){
 const rows=row.outcomes===undefined?[]:row.outcomes;
 if(!Array.isArray(rows)||rows.length>16||row.decision!=='approve'&&rows.length)throw Error('Invalid outcomes');
 const ids=new Set();let state='initial',project=null;
 for(const event of rows){
  if(!event||typeof event!=='object'||!Object.prototype.hasOwnProperty.call(phases,event.phase)||!phases[event.phase].includes(event.status)||typeof event.event_id!=='string'||! /^[a-f0-9]{32}$/.test(event.event_id)||ids.has(event.event_id)||typeof event.project_hash!=='string'||! /^[a-f0-9]{64}$/.test(event.project_hash)||!Number.isFinite(event.observed_at)||event.observed_at<0||event.observed_at>253402300799||typeof event.edit_id!=='string'||! /^(?:|(?:group-)?[a-f0-9]{32})$/.test(event.edit_id)||!Object.prototype.hasOwnProperty.call(rollbacks,event.rollback)||event.evidence_sha256!==null&&(typeof event.evidence_sha256!=='string'||! /^[a-f0-9]{64}$/.test(event.evidence_sha256)))throw Error('Invalid outcome');
  if(project!==null&&project!==event.project_hash)throw Error('Mixed execution');project=event.project_hash;ids.add(event.event_id);
  if(event.phase==='started'&&state==='initial')state='running';else if(event.phase==='result'&&state==='running')state='finished';else if(event.phase==='undo_started'&&state==='finished')state='undo';else if(event.phase==='undo_result'&&state==='undo')state='finished';else throw Error('Invalid sequence');
  if(event.status==='passed'&&(!event.checks||event.checks.executed<1||event.checks.executed!==event.checks.planned||event.checks.passed!==event.checks.executed||event.checks.failed!==0||event.checks.cancelled!==0||event.checks.timed_out!==0))throw Error('Contradictory passed evidence');
  if(event.checks!==null){const names=['planned','executed','passed','failed','cancelled','timed_out'];if(typeof event.checks!=='object'||!names.every(k=>Number.isInteger(event.checks[k])&&event.checks[k]>=0&&event.checks[k]<=64))throw Error('Invalid native counts');}
 }
 return rows;
}
function paragraph(parent,text){const p=document.createElement('p');p.textContent=text;parent.append(p);}
const revisionKinds=['runtime_config','idle_config','integration','integration_access','remote_syntax','scheduled_syntax'];
function revisions(row,capability=false){
 const source=capability?row.capability_revisions:row.revisions,links=source===undefined?[]:source;
 if(!Array.isArray(links)||links.length>16)throw Error('Invalid revision links');
 const fields=['parent_id','parent_fingerprint','child_id','child_fingerprint','child_state','created_at',...(capability?['kind']:[])],ids=new Set();
 for(const link of links){
  if(!link||typeof link!=='object'||Object.keys(link).length!==fields.length||!fields.every(k=>Object.prototype.hasOwnProperty.call(link,k))||!['parent_id','child_id'].every(k=>typeof link[k]==='string'&&/^[a-f0-9]{32}$/.test(link[k]))||!['parent_fingerprint','child_fingerprint'].every(k=>typeof link[k]==='string'&&/^[a-f0-9]{64}$/.test(link[k]))||link.parent_id===link.child_id||ids.has(link.child_id)||!['recorded','unrecorded'].includes(link.child_state)||!Number.isFinite(link.created_at)||link.created_at<0||link.created_at>253402300799)throw Error('Invalid revision relationship');
  const parent=link.parent_id===row.id&&link.parent_fingerprint===row.fingerprint;
  const child=link.child_id===row.id&&link.child_fingerprint===row.fingerprint;
  if((capability?(!revisionKinds.includes(row.kind)||link.kind!==row.kind):row.kind!=='project_edit')||!parent&&!child||parent&&row.decision!=='request_changes'||child&&link.child_state!=='recorded')throw Error('Revision identity mismatch');
  ids.add(link.child_id);
 }
 return links;
}
function revisionTimeline(item,row,capability=false){
 const links=revisions(row,capability);if(!links.length)return;
 if(capability)paragraph(item,labels[row.kind]+' revision chain.');
 paragraph(item,'Review reference: '+row.id.slice(0,8)+'.');
 for(const link of links)paragraph(item,link.child_id===row.id
  ? 'Replacement review for requested changes in review '+link.parent_id.slice(0,8)+'. This review required its own decision.'
  : 'Replacement review '+link.child_id.slice(0,8)+' · '+(link.child_state==='recorded'?'a separate decision is recorded.':'no decision is recorded; its pending authority is not restored by history.'));
}
function sharedEvidence(row){
 const groups=row.shared_evidence===undefined?[]:row.shared_evidence;
 if(!Array.isArray(groups)||groups.length>16)throw Error('Invalid shared evidence');
 if(groups.length&&(!['remote_syntax','scheduled_syntax'].includes(row.kind)||row.decision!=='approve'||!/^[a-f0-9]{32}$/.test(row.id)||!/^[a-f0-9]{64}$/.test(row.fingerprint)))throw Error('Invalid shared evidence kind');
 const terminal=capabilityEvents(row).at(-1),references=new Set(),fields=['reference','kind','status','linked_decisions','related_decisions','truncated','read_only'];
 for(const group of groups){
  if(!group||typeof group!=='object'||Object.keys(group).length!==fields.length||!fields.every(k=>Object.prototype.hasOwnProperty.call(group,k))||typeof group.reference!=='string'||!/^[a-f0-9]{64}$/.test(group.reference)||references.has(group.reference)||group.kind!=='worker_result'||!['checks_passed','checks_failed','tool_failed'].includes(group.status)||terminal?.status!==group.status||!Number.isSafeInteger(group.linked_decisions)||group.linked_decisions<2||!Array.isArray(group.related_decisions)||group.related_decisions.length!==Math.min(group.linked_decisions-1,16)||group.truncated!==(group.linked_decisions>17)||group.read_only!==true)throw Error('Invalid shared evidence reference');
  const ids=new Set();
  for(const related of group.related_decisions){
   if(!related||typeof related!=='object'||Object.keys(related).length!==2||typeof related.id!=='string'||!/^[a-f0-9]{32}$/.test(related.id)||typeof related.fingerprint!=='string'||!/^[a-f0-9]{64}$/.test(related.fingerprint)||related.id===row.id||ids.has(related.id))throw Error('Invalid related decision');
   ids.add(related.id);
  }
  references.add(group.reference);
 }
 return groups;
}
function evidenceTimeline(item,row){
 for(const group of sharedEvidence(row)){
  paragraph(item,'Shared native worker observation · '+capabilityLabels[group.status]+'. Reference: '+group.reference+'.');
  paragraph(item,group.linked_decisions+' decisions link to this same recorded result. Other review references: '+group.related_decisions.map(ref=>ref.id.slice(0,8)).join(', ')+(group.truncated?' (additional references omitted).':'.'));
  paragraph(item,'Read-only provenance for this owner’s exact worker result. These links do not establish separate executions, authorize new work or replay a job.');
 }
}
const capabilityLabels={started:'Capability execution started',settings_saved:'Settings saved; future behavior has not been verified',access_granted:'Access issued; this does not confirm a provider action',adapter_returned:'Integration adapter returned; result correctness is not independently verified',waiting:'Waiting at the approved destination; no execution confirmed',deferred:'Execution deferred',unconfirmed:'Execution unconfirmed; do not infer success or repeat execution',unknown:'Outcome unknown',cancelled:'Waiting job cancelled',expired:'Waiting permission expired',checks_passed:'Native worker checks passed',checks_failed:'Native worker checks found failures',tool_failed:'Verification tool failed; this is not a code-defect finding'};
const capabilityTerminal=new Set(['settings_saved','access_granted','adapter_returned','cancelled','expired','checks_passed','checks_failed','tool_failed']);
function capabilityEvents(row){
 const rows=row.capability_outcomes===undefined?[]:row.capability_outcomes;
 if(!Array.isArray(rows)||rows.length>16||row.decision!=='approve'&&rows.length)throw Error('Invalid capability outcomes');
 const asyncKind=['remote_syntax','scheduled_syntax'].includes(row.kind),allowed=['started','unknown'];
 if(asyncKind)allowed.push('waiting','deferred','unconfirmed','cancelled','expired','checks_passed','checks_failed','tool_failed');
 else if(['runtime_config','idle_config'].includes(row.kind))allowed.push('settings_saved');
 else if(row.kind==='integration_access')allowed.push('access_granted');
 else if(row.kind==='integration')allowed.push('adapter_returned');
 else if(rows.length)throw Error('Invalid capability kind');
 if(rows.length&&events(row).length)throw Error('Mixed capability records');
 const ids=new Set(),fields=['event_id','phase','observed_at','status','job_id','request_digest','checks','evidence_sha256'];let previous=null;
 for(const event of rows){
  if(!event||typeof event!=='object'||Object.keys(event).length!==fields.length||!fields.every(k=>Object.prototype.hasOwnProperty.call(event,k))||typeof event.event_id!=='string'||!/^[a-f0-9]{32}$/.test(event.event_id)||ids.has(event.event_id)||!allowed.includes(event.status)||!['started','observed'].includes(event.phase)||(event.phase==='started')!==(event.status==='started')||!Number.isFinite(event.observed_at)||event.observed_at<0||event.observed_at>253402300799)throw Error('Invalid capability event');
  if(asyncKind?(typeof event.job_id!=='string'||!/^[a-f0-9]{32}$/.test(event.job_id)||typeof event.request_digest!=='string'||!/^[a-f0-9]{64}$/.test(event.request_digest)):(event.job_id!==''||event.request_digest!==''))throw Error('Invalid capability binding');
  if(previous?(event.phase==='started'||capabilityTerminal.has(previous.status)||event.job_id!==previous.job_id||event.request_digest!==previous.request_digest):event.phase!=='started')throw Error('Invalid capability sequence');
  if(event.evidence_sha256!==null&&(typeof event.evidence_sha256!=='string'||!/^[a-f0-9]{64}$/.test(event.evidence_sha256)))throw Error('Invalid evidence digest');
  const c=event.checks,names=['planned','executed','passed','failed','cancelled','timed_out'];
  if(c!==null&&(typeof c!=='object'||Object.keys(c).length!==names.length||!names.every(k=>Number.isInteger(c[k])&&c[k]>=0&&c[k]<=64)||c.executed>c.planned||c.passed+c.failed+c.cancelled+c.timed_out!==c.executed))throw Error('Invalid capability counts');
  if(['started','unknown'].includes(event.status)&&(c!==null||event.evidence_sha256!==null))throw Error('Unobserved capability evidence');
  if(['checks_passed','checks_failed'].includes(event.status)){
   if(!c||!event.evidence_sha256||c.planned===0||(event.status==='checks_passed')!==(c.executed===c.planned&&c.passed===c.planned)||event.status==='checks_failed'&&c.failed===0)throw Error('Contradictory capability checks');
  }else if(c!==null)throw Error('Unexpected capability checks');
  if(capabilityTerminal.has(event.status)&&event.evidence_sha256===null)throw Error('Missing native evidence');
  ids.add(event.event_id);previous=event;
 }
 return rows;
}
function capabilityTimeline(item,rows){
 const ordered=document.createElement('ol');item.append(ordered);
 for(const event of rows){const li=document.createElement('li');paragraph(li,new Date(event.observed_at*1000).toLocaleString()+' · '+capabilityLabels[event.status]);
  if(event.checks){const c=event.checks;paragraph(li,`Native worker checks: planned ${c.planned}, executed ${c.executed}, passed ${c.passed}, failed ${c.failed}, cancelled ${c.cancelled}, timed out ${c.timed_out}.`);}
  ordered.append(li);
 }
 if(rows[rows.length-1].phase==='started')paragraph(item,'Execution outcome unconfirmed. Reading history cannot resume or repeat work.');
 paragraph(item,'Recorded native observations only; not model acceptance or new execution authority.');
}
function timeline(item,row){
 const rows=events(row);
 if(row.decision!=='approve'){paragraph(item,'This decision authorized no execution.');return;}
 const capabilities=capabilityEvents(row);if(capabilities.length){capabilityTimeline(item,capabilities);return;}
 if(!rows.length){paragraph(item,'No linked execution evidence. Approval alone does not confirm execution or success.');return;}
 const timeline=document.createElement('ol');item.append(timeline);
 for(const event of rows){const li=document.createElement('li');paragraph(li,new Date(event.observed_at*1000).toLocaleString()+' · '+(event.phase==='undo_started'?'Manual restoration started':event.status==='passed'&&(!event.checks||event.checks.executed===0)?'Execution reported passed, but native check evidence is unavailable; behavior is unverified':statuses[event.status]));
  if(event.checks){const c=event.checks;paragraph(li,`Native checks: planned ${c.planned}, executed ${c.executed}, passed ${c.passed}, failed ${c.failed}, cancelled ${c.cancelled}, timed out ${c.timed_out}.`);if(c.executed===0)paragraph(li,'No executed checks; behavior is unverified.');}
  else if(event.phase==='result')paragraph(li,'No native check summary; behavior is unverified.');
  if(event.phase==='result')paragraph(li,'Rollback: '+rollbacks[event.rollback]+'.');timeline.append(li);}
 const last=rows[rows.length-1];
 if(last.phase==='started')paragraph(item,'Execution outcome unconfirmed. Work is not automatically resumed.');
 if(last.phase==='undo_started')paragraph(item,'Restoration unconfirmed. Work is not automatically resumed.');
 paragraph(item,'Recorded evidence only; not model acceptance or execution authority.');
}
function clear(message){generation++;reading=false;list.replaceChildren();status.textContent=message;controls();}
function controls(){refresh.disabled=!ready()||reading;}
async function load(){
 if(!ready()||!box.open||reading)return;
 const version=generation;reading=true;controls();
 try{
  const data=await api('/api/decisions/history');if(version!==generation||!ready()||!box.open)return;
  if(!data||data.schema_version!==1||data.read_only!==true||!Array.isArray(data.items)||data.items.length>50)throw Error('Invalid history');
  for(const row of data.items)if(!row||!['id','fingerprint','session','kind','feedback'].every(k=>typeof row[k]==='string')||!Object.prototype.hasOwnProperty.call(decisions,row.decision)||row.feedback.length>2000||!Number.isFinite(row.decided_at)||row.decided_at<0)throw Error('Invalid receipt');
  for(const row of data.items){events(row);capabilityEvents(row);revisions(row);revisions(row,true);sharedEvidence(row);}
  list.replaceChildren();for(const row of data.items){const item=document.createElement('li'),title=document.createElement('p');title.textContent=(Object.prototype.hasOwnProperty.call(labels,row.kind)?labels[row.kind]:'Owner decision')+' · '+decisions[row.decision]+' · '+new Date(row.decided_at*1000).toLocaleString();item.append(title);if(row.feedback){const note=document.createElement('p');note.textContent=row.feedback;item.append(note);}revisionTimeline(item,row);revisionTimeline(item,row,true);timeline(item,row);evidenceTimeline(item,row);list.append(item);}
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
