(function () {
'use strict';
const el=id=>document.getElementById(id);
let state={busy:false,online:false,closed:false}, pending=null, sending=false;
let resourceExpiry=null;
const say=text=>{el('workerJobStatus').textContent=text;};
function resourceText(report) {
  const facts=report?.observations || {}, number=(value,unit)=>typeof value==='number' && Number.isFinite(value) ? value.toFixed(1)+unit : 'unknown';
  return 'Surface resources: '+(report?.decision || 'unknown')+' · RAM available '+number(facts.available_ram_gib,' GiB')+
    ' · CPU '+number(facts.cpu_percent,'%')+' · Disk free '+number(facts.free_disk_gib,' GiB')+
    ' · Battery '+number(facts.battery_percent,'%')+' · '+(facts.plugged_in===true ? 'external power' : facts.plugged_in===false ? 'battery power' : 'power unknown')+'.';
}
function resources(report) {
  if(resourceExpiry!==null)clearTimeout(resourceExpiry);
  resourceExpiry=null;
  const box=el('workerResources');box.hidden=!report;box.textContent='';
  if(!report)return;
  const remaining=report.expires_at*1000-Date.now();
  if(report.authenticated!==true || !Number.isFinite(remaining) || remaining<=0){
    box.textContent='Resource reading unavailable or expired. Refresh for a current reading.';return;
  }
  box.textContent=resourceText(report)+' Signed reading; valid until '+new Date(report.expires_at*1000).toLocaleTimeString()+
    '. Resources are checked again after approval.';
  resourceExpiry=setTimeout(()=>{resourceExpiry=null;box.textContent='Resource reading expired. Refresh for a current reading. Approval always checks again.';},Math.min(remaining,30000));
}
function controls() {
  const locked=state.busy || !state.online || state.closed || sending;
  for(const id of ['workerFiles','workerJobId','workerRefresh','workerRecover']) el(id).disabled=locked;
  el('workerPrepare').disabled=locked || !!pending;
  for(const id of ['workerApprove','workerDeny']) el(id).disabled=locked || !pending;
}
function review(row) {
  pending=row?.kind==='remote_syntax' ? row : null;
  const box=el('workerReview'); box.replaceChildren(); box.hidden=!pending;
  el('workerDecisions').hidden=!pending;
  if(pending) {
    const r=pending.review || {}, target=r.destination || {};
    for(const text of [r.action,r.reason,r.benefit,r.effects,
      'Destination: '+target.device_name+' · '+target.device_id+' · account '+target.account_id,
      'Worker version: '+target.worker_digest, r.duration]) {
      const p=document.createElement('p');p.textContent=String(text || '');box.append(p);
    }
    if(r.resource_admission) {
      const p=document.createElement('p');p.textContent=resourceText(r.resource_admission)+
        ' Snapshot at review; a fresh check and local worker admission are required before execution.';box.append(p);
      resources(r.resource_admission);
    }
    for(const file of r.files || []) {
      const heading=document.createElement('p');heading.textContent=file.path+' · '+file.utf8_bytes+' bytes · SHA256 '+file.sha256;
      const source=document.createElement('pre');source.className='code-output';source.textContent=file.content;box.append(heading,source);
    }
    say('Review the exact source and destination. Approval sends this snapshot once.');
    window.PrometheusWorkspace?.show('Code');
  }
  controls();
}
async function send(body) {
  if(state.busy || !state.online || state.closed || sending)return;
  sending=true;controls();
  try {
    const context=typeof session==='string' && session ? {session} : {};
    if(!await submitAction({...context,...body}))say('The request did not start. Check the connection.');
  }catch(error){say(error.message || 'Worker request failed.');}
  finally{sending=false;controls();}
}
el('workerPrepare').onclick=()=>{
  const context=window.PrometheusWorkspace?.context();
  if(!context?.project || context.dirty || context.stale){say('Open a project and save or discard its editor changes first.');return;}
  return send({mode:'remote_syntax_prepare',project:context.project,remote_files:el('workerFiles').value});
};
el('workerRefresh').onclick=async()=>{
  if(state.busy || !state.online || state.closed || sending)return;
  try {
    const result=await api('/api/approvals/pending');
    if(state.closed || !state.online)return;
    review(result.approval);
    if(result.approval && !pending){say('Another change is waiting for your decision.');return;}
    if(!pending)await send({mode:'remote_syntax_info'});
  }catch(error){say(error.message || 'Could not refresh the worker review.');}
};
function decide(decision){if(pending)return send({mode:'approval_decision',approval_id:pending.id,
  approval_fingerprint:pending.fingerprint,approval_decision:decision,session:pending.session});}
el('workerApprove').onclick=()=>decide('approve');el('workerDeny').onclick=()=>decide('reject');
el('workerRecover').onclick=()=>send({mode:'remote_syntax_recover',remote_job_id:el('workerJobId').value.trim()});
window.addEventListener('prometheus-busy',event=>{state={...state,...event.detail};controls();});
window.addEventListener('prometheus-result',event=>{
  const result=event.detail || {},data=result.details || {};
  if(state.closed || result.origin==='observed')return;
  if(data.approval_resolved===pending?.id)review(null);
  if(data.approval)review(data.approval);
  if(data.remote_worker && !pending) {
    const status=data.remote_worker;
    resources(status.resource_admission);
    say(status.ready ? 'Ready for exact review: '+status.target.device_name+'. Local admission is checked before execution.' : status.reason);
  }
  if(data.remote_syntax){
    const job=data.remote_syntax;el('workerJobId').value=job.job_id || '';
    say('Job '+job.job_id+': '+job.state+'. '+(job.state==='rejected' ? 'Review denied. No files were transferred and no job was claimed.' : job.state==='cancelled' ?
      'Cancelled before claiming work. No source was transferred.' : job.state==='deferred' ?
      'No source transferred or job claimed. '+(job.reasons || []).join(' ')+' Prepare a new review when the computer is ready.' : job.evidence ? 'Syntax evidence: '+job.evidence.status+'.' :
      'No authenticated terminal result. Use recovery to query this job; it will not run again.'));
    el('workerResult').textContent=JSON.stringify(job,null,2);
  }
  if(result.error && (result.action?.startsWith('remote_syntax_') || pending && result.action==='approval_decision')) {
    say(result.error+' Refresh the review before another decision.');
    if(result.action==='approval_decision')review(null);
  }
  controls();
});
window.addEventListener('prometheus-auth-lost',()=>{state={busy:false,online:false,closed:true};review(null);
  resources(null);el('workerResult').textContent='';say('Unlock Prometheus to inspect worker jobs.');});
controls();
})();
