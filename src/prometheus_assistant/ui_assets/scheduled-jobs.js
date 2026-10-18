(function () {
'use strict';
const el=id=>document.getElementById(id), interval=30000;
let state={busy:false,online:false,closed:false}, pending=null, otherReview=false;
let sending=false, reading=false, timer=null, version=0, readId=0, loaded=false, attempted=false, refreshDue=false;
let lastRead=0, jobButtons=[];
const unlocked=()=>state.online && !state.closed;
const ready=()=>unlocked() && !state.busy && !sending;
const say=text=>{el('scheduledSyntaxStatus').textContent=text;};
const paragraph=(box,text)=>{const p=document.createElement('p');p.textContent=String(text || '');box.append(p);};
const date=value=>typeof value==='number' && Number.isFinite(value) ? new Date(value*1000).toLocaleString() : 'unknown';
const gib=value=>typeof value==='number' && Number.isFinite(value) ? value.toFixed(2)+' GiB' : 'unknown';
function controls() {
  el('scheduledSyntaxFiles').disabled=!ready();
  if(el('scheduledSyntaxPlacement'))el('scheduledSyntaxPlacement').disabled=!ready() || !!pending || otherReview;
  if(el('scheduledVerificationOperation'))el('scheduledVerificationOperation').disabled=!ready() || !!pending || otherReview;
  el('scheduledSyntaxPrepare').disabled=!ready() || !!pending || otherReview;
  el('scheduledSyntaxRefresh').disabled=!ready() || reading;
  for(const id of ['scheduledSyntaxApprove','scheduledSyntaxDeny'])el(id).disabled=!ready() || !pending;
  for(const item of jobButtons)item.button.disabled=!ready() || item.expired;
}
function stopTimer(){if(timer!==null)clearTimeout(timer);timer=null;}
function schedule() {
  if(timer!==null || !unlocked() || document.hidden)return;
  timer=setTimeout(()=>{timer=null;if(ready() && !document.hidden)void refresh();else schedule();},interval);
}
function review(row,focus=false) {
  pending=row?.kind==='scheduled_syntax' ? row : null;
  otherReview=!!row && !pending;
  const box=el('scheduledSyntaxReview');box.replaceChildren();box.hidden=!pending;
  el('scheduledSyntaxDecisions').hidden=!pending;
  if(pending) {
    const r=pending.review || {},target=r.destination || {};
    for(const text of [r.action,r.reason,r.benefit,r.effects,
      'Destination: '+target.device_name+' · '+target.device_id+' · account '+target.account_id,
      'Worker version: '+target.worker_digest,r.duration])paragraph(box,text);
    if(r.tool)paragraph(box,'Ruff '+r.tool.version+' · SHA256 '+r.tool.sha256+' · profile '+r.profile+'.');
    if(r.limits)paragraph(box,'Execution limits: '+r.limits.timeout_seconds+' seconds per job, '+
      r.limits.child_memory_mib+' MiB address space and '+r.limits.child_cpu_seconds+' CPU seconds per process; '+
      r.limits.max_diagnostics+' diagnostics maximum.');
    for(const candidate of r.placement?.candidates || []) {
      paragraph(box,candidate.device_name+': '+candidate.decision+' · RAM headroom after reserves and estimate '+gib(candidate.headroom_gib)+
        ' · job RAM estimate '+gib(candidate.estimated_ram_gib)+'. '+(candidate.reasons || []).join(' '));
    }
    for(const file of r.files || []) {
      paragraph(box,file.path+' · '+file.utf8_bytes+' bytes · SHA256 '+file.sha256);
      const source=document.createElement('pre');source.className='code-output';source.textContent=file.content;box.append(source);
    }
    say('Review the exact source, selected computer and waiting permission. This approval cannot switch destinations.');
    if(focus)window.PrometheusWorkspace?.show('Code');
  }
  controls();
}
function renderJobs(jobs) {
  const box=el('scheduledSyntaxQueue');box.replaceChildren();jobButtons=[];
  if(!jobs.length)paragraph(box,'No saved approved syntax jobs.');
  for(const job of jobs) {
    const card=document.createElement('section');card.className='workspace-card';
    const heading=document.createElement('h4');heading.textContent=(job.destination || 'Approved destination')+' · '+job.state;card.append(heading);
    paragraph(card,'Job '+job.job_id+' · destination '+job.device_id);
    if(job.operation==='python-ruff-check-v1')paragraph(card,'Operation: fixed-profile Ruff lint without source execution.');
    paragraph(card,'Files: '+(job.files || []).join(', '));
    if(job.approved_at)paragraph(card,'Approved '+date(job.approved_at)+'. Waiting permission ends '+date(job.expires_at)+'.');
    if(job.reasons?.length)paragraph(card,job.reasons.join(' '));
    if(job.state==='waiting') {
      paragraph(card,'Saved for this exact destination. Prometheus rechecks resources while open, unlocked and idle.');
      const expired=typeof job.expires_at==='number' && job.expires_at*1000<=Date.now();
      if(expired)paragraph(card,'The waiting deadline has passed. Refresh to confirm the saved state.');
      jobButton(card,'Cancel waiting job','scheduled_syntax_cancel',job.job_id,expired);
    } else if(job.state==='unconfirmed') {
      paragraph(card,'An execution claim exists, but no confirmed terminal result is saved. Recovery only queries that original job; it never reruns it.');
      jobButton(card,'Recover result without rerunning','scheduled_syntax_recover',job.job_id);
    } else if(job.state==='expired' || job.state==='cancelled') {
      paragraph(card,'This job will not start. Prepare a new review to request another check.');
    }
    if(job.result?.evidence) {
      const lint=job.result.receipt?.operation==='python-ruff-check-v1';
      paragraph(card,(lint ? 'Lint evidence: ' : 'Syntax evidence: ')+job.result.evidence.status+'.');
      if(lint) {
        paragraph(card,'Tool result: '+job.result.receipt.status+'.');
        for(const d of job.result.receipt.diagnostics || [])paragraph(card,d.path+':'+d.line+':'+d.column+' '+d.code+' '+d.message);
      }
      if(['passed','failed'].includes(job.result.evidence.status)) {
        for(const check of job.result.receipt?.checks || []) {
          paragraph(card,check.path+': '+(lint ? (check.passed===true ? 'lint passed' : 'lint did not pass') :
            (check.passed===true ? 'syntax passed' : 'syntax check failed'))+'.');
        }
      }
      for(const reason of job.result.evidence.reasons || [])paragraph(card,reason);
      paragraph(card,lint ? 'Lint does not execute your code or qualify a model reviewer. Runtime behavior needs separate tests.' :
        'This checks Python syntax without running your code. Runtime behavior still needs its own tests.');
    }
    box.append(card);
  }
  controls();
}
function jobButton(card,label,mode,jobId,expired=false) {
  const button=document.createElement('button');button.textContent=label;
  button.onclick=()=>send({mode,scheduled_job_id:jobId});card.append(button);jobButtons.push({button,expired});
}
async function refresh() {
  if(!ready() || reading || document.hidden)return;
  stopTimer();reading=true;attempted=true;refreshDue=false;const ticket=++readId,current=version;controls();
  try {
    const [queue,approvals]=await Promise.all([api('/api/syntax-queue'),api('/api/approvals/pending')]);
    if(!unlocked() || current!==version || ticket!==readId)return;
    renderJobs(queue.jobs || []);review(approvals.approval);
    loaded=true;refreshDue=false;lastRead=Date.now();
    el('scheduledSyntaxQueueStatus').textContent='Last checked '+new Date(lastRead).toLocaleTimeString()+
      '. Refreshes about every 30 seconds while visible and idle. '+(queue.auto_resume || 'Unlock Prometheus after restart to resume saved waiting work.');
    if(otherReview)say('Another change is waiting for your decision. Resolve it before preparing an automatic destination review.');
  }catch(error){
    if(unlocked() && current===version && ticket===readId)el('scheduledSyntaxQueueStatus').textContent=
      'Saved-job status is not current. '+(error.message || 'Could not read waiting jobs.')+' Use Refresh to try again.';
  }finally{
    if(ticket===readId){reading=false;controls();if(ready() && refreshDue)void refresh();else schedule();}
  }
}
async function send(body) {
  if(!ready())return;
  version++;sending=true;controls();
  try {
    const context=typeof session==='string' && session ? {session} : {};
    if(!await submitAction({...context,...body}))say('The request did not start. Check the connection and refresh the review.');
  }catch(error){say(error.message || 'The waiting-job request failed.');}
  finally{
    sending=false;controls();
    if(ready() && refreshDue && !reading)void refresh();else schedule();
  }
}
el('scheduledSyntaxPrepare').onclick=()=>{
  const context=window.PrometheusWorkspace?.context();
  if(!context?.project || context.dirty || context.stale){say('Open a project and save or discard its editor changes first.');return;}
  const files=el('scheduledSyntaxFiles').value,lines=files.trim().split(/\r?\n/);
  if(!files.trim() || lines.length>8 || files.length>4000 || lines.some(line=>!line.trim() || !line.trim().endsWith('.py'))){
    say('Choose one to eight saved .py files, one relative path per line.');return;
  }
  return send({mode:'scheduled_syntax_prepare',project:context.project,remote_files:lines.map(line=>line.trim()).join('\n'),
    scheduled_placement:el('scheduledSyntaxPlacement')?.value || 'available-headroom-v1',
    scheduled_operation:el('scheduledVerificationOperation')?.value || 'python-syntax-check'});
};
el('scheduledSyntaxRefresh').onclick=()=>refresh();
function decide(decision) {
  if(pending)return send({mode:'approval_decision',approval_id:pending.id,approval_fingerprint:pending.fingerprint,
    approval_decision:decision,session:pending.session});
}
el('scheduledSyntaxApprove').onclick=()=>decide('approve');el('scheduledSyntaxDeny').onclick=()=>decide('reject');
window.addEventListener('prometheus-busy',event=>{
  const wasUnlocked=unlocked();state={...state,...event.detail};controls();
  if(!unlocked()){
    stopTimer();
    if(wasUnlocked)el('scheduledSyntaxQueueStatus').textContent='Connection unavailable. The displayed job status may be out of date.';
    return;
  }
  if(ready() && (!attempted || !wasUnlocked || refreshDue))void refresh();
  else schedule();
});
window.addEventListener('prometheus-result',event=>{
  if(!unlocked())return;
  const result=event.detail || {},data=result.details || {};
  if(result.origin==='observed'){
    if(result.action?.startsWith('scheduled_syntax_')){refreshDue=true;if(ready())void refresh();}
    return;
  }
  version++;
  if(data.approval_resolved===pending?.id)review(null);
  if(data.approval)review(data.approval,true);
  if(data.scheduled_queue)renderJobs(data.scheduled_queue);
  if(data.scheduled_syntax) {
    const job=data.scheduled_syntax;
    const extra=job.state==='rejected' ? 'Review denied; no job was queued or sent.' : job.state==='waiting' ?
      'Saved for the exact approved destination. Waiting survives restart; unlock to resume.' : job.state==='unconfirmed' ?
      'The result is uncertain. Recovery queries the original job without rerunning it.' : (job.reasons || []).join(' ');
    say('Job '+job.job_id+': '+job.state+'. '+extra);
    refreshDue=true;
  }
  if(result.error && (result.action?.startsWith('scheduled_syntax_') || pending && result.action==='approval_decision')) {
    say(result.error+' Refresh the saved jobs and review before trying again.');
    if(result.action==='approval_decision')review(null);
    refreshDue=true;
  }
  if(ready() && refreshDue)void refresh();
  controls();
});
window.addEventListener('prometheus-auth-lost',()=>{
  version++;readId++;reading=false;sending=false;loaded=false;attempted=false;refreshDue=false;lastRead=0;
  state={busy:false,online:false,closed:true};stopTimer();review(null);renderJobs([]);el('scheduledSyntaxQueue').replaceChildren();
  el('scheduledSyntaxFiles').value='';say('Unlock Prometheus to inspect automatic syntax jobs.');
  el('scheduledSyntaxQueueStatus').textContent='Waiting work is paused until this owner unlocks Prometheus.';
});
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){stopTimer();return;}
  if(ready() && (!loaded || refreshDue || Date.now()-lastRead>=interval))void refresh();else schedule();
});
controls();
})();
