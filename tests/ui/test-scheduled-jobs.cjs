'use strict';
// DOM contracts for exact approval, durable queue recovery, and bounded refresh.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {setup}=require('./test-workspace.cjs');
const script=fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/scheduled-jobs.js'),'utf8');
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const flatten=element=>[element.textContent,...element.children.flatMap(flatten)].join('\n');
const buttons=element=>element.children.flatMap(child=>child.tagName==='BUTTON'?[child]:buttons(child));
function fixture() {
  const f=setup(),timers=new Map(),calls=[],documentEvents={};let next=0;
  f.context.document.hidden=false;
  f.context.document.addEventListener=(name,fn)=>{documentEvents[name]=fn;};
  f.context.setTimeout=(callback,delay)=>{const id=++next;timers.set(id,{callback,delay});return id;};
  f.context.clearTimeout=id=>timers.delete(id);
  f.context.session='current-session';
  let context={project:'C:\\Project',dirty:false,stale:false},approval=null,jobs=[];
  f.context.window.PrometheusWorkspace.context=()=>context;
  f.context.api=async route=>{
    calls.push(route);
    if(route==='/api/syntax-queue')return {jobs,interval_seconds:30,auto_resume:'Open and unlock to resume.'};
    if(route==='/api/approvals/pending')return {approval};
    throw new Error('Unexpected route '+route);
  };
  vm.runInNewContext(script,f.context,{filename:'scheduled-jobs.js'});
  return {...f,calls,timers,
    contextProject(value){context={...context,...value};},
    setApproval(value){approval=value;},setJobs(value){jobs=value;},
    busy(value=false){f.emit('prometheus-busy',{busy:value,online:true,closed:false});},
    visible(value){f.context.document.hidden=!value;documentEvents.visibilitychange();},
    async timer(){assert.equal(timers.size,1,'one refresh timer');const [id,row]=[...timers][0];timers.delete(id);assert.equal(row.delay,30000);row.callback();await settle();}
  };
}
const future=()=>Math.floor(Date.now()/1000)+3600;
function job(state='waiting',more={}) {return {job_id:'a'.repeat(32),state,destination:'Surface',device_id:'surface-id',
  files:['a.py'],approved_at:Math.floor(Date.now()/1000),expires_at:future(),reasons:['CPU is busy.'],result:null,...more};}
function row(device='Surface') {return {id:'exact-id',session:'review-session',fingerprint:'exact-fingerprint',kind:'scheduled_syntax',
  review:{action:'Approve a syntax check',reason:'Best available headroom',benefit:'Keep work responsive',
    effects:'Retain exact source locally; compile without executing it.',duration:'One execution within one hour. Waiting survives restart. No destination switching.',
    destination:{device_name:device,device_id:device+'-id',account_id:device==='Dell'?'local':'surface-account',worker_digest:'worker-digest'},
    placement:{candidates:[{device_name:device,decision:'ready',headroom_gib:4.25,estimated_ram_gib:0.375,reasons:[]}]},
    files:[{path:'a.py',content:'<script>untrusted source</script>',utf8_bytes:33,sha256:'exact-source-hash'}]}};}
(async()=>{
  // A fresh unlocked page reads the durable queue without posting any execution request.
  const f=fixture(),e=f.elements;f.setJobs([job()]);assert(e.scheduledSyntaxPrepare.disabled);
  f.busy();await settle();
  assert.deepEqual(f.calls,['/api/syntax-queue','/api/approvals/pending']);assert.equal(f.submitted.length,0);
  assert.match(flatten(e.scheduledSyntaxQueue),/Surface · waiting/);assert.match(flatten(e.scheduledSyntaxQueue),/CPU is busy/);
  assert.match(flatten(e.scheduledSyntaxQueue),/exact destination/);assert.match(e.scheduledSyntaxQueueStatus.textContent,/30 seconds/);
  const timerId=[...f.timers.keys()][0];f.busy();f.busy();assert.equal([...f.timers.keys()][0],timerId,'normal status events must not postpone polling forever');
  await buttons(e.scheduledSyntaxQueue)[0].click();
  assert.equal(f.submitted.at(-1).mode,'scheduled_syntax_cancel');assert.equal(f.submitted.at(-1).scheduled_job_id,'a'.repeat(32));
  assert.equal(f.submitted.at(-1).remote_files,undefined);
  // Files edited after preparation cannot be substituted into the exact approval.
  e.scheduledSyntaxPlacement.value='remote-lightweight-v1';
  e.scheduledSyntaxFiles.value='a.py\nb.py';await e.scheduledSyntaxPrepare.click();
  assert.equal(f.submitted.at(-1).scheduled_placement,'remote-lightweight-v1');
  assert.equal(f.submitted.at(-1).project,'C:\\Project');assert.equal(f.submitted.at(-1).mode,'scheduled_syntax_prepare');
  f.result('scheduled_syntax_prepare',{approval:row()});
  assert(!e.scheduledSyntaxReview.hidden);assert(e.scheduledSyntaxPrepare.disabled);
  assert(e.scheduledSyntaxPlacement.disabled);
  const review=flatten(e.scheduledSyntaxReview);
  assert.match(review,/Surface-id · account surface-account/);assert.match(review,/worker-digest/);
  assert.match(review,/exact-source-hash/);assert.match(review,/4.25 GiB/);assert.match(review,/one hour/);
  assert.equal(e.scheduledSyntaxReview.children.at(-1).textContent,'<script>untrusted source</script>');
  e.scheduledSyntaxFiles.value='changed.py';await e.scheduledSyntaxApprove.click();
  assert.deepEqual(f.submitted.at(-1),{session:'review-session',mode:'approval_decision',approval_id:'exact-id',
    approval_fingerprint:'exact-fingerprint',approval_decision:'approve'});
  // Result refreshes read saved state; they do not dispatch a queue tick from the browser.
  f.setJobs([job()]);f.result('approval_decision',{approval_resolved:'exact-id',scheduled_syntax:job()});await settle();
  assert(e.scheduledSyntaxReview.hidden);assert.match(e.scheduledSyntaxStatus.textContent,/survives restart/);
  assert(!f.submitted.some(request=>request.mode==='scheduled_syntax_tick'));
  // Reload recovers waiting jobs and the current exact destination review from authenticated reads.
  const restored=fixture();restored.setJobs([job()]);restored.setApproval(row('Dell'));restored.busy();await settle();
  assert.equal(restored.submitted.length,0);assert.match(flatten(restored.elements.scheduledSyntaxReview),/Dell-id · account local/);
  await restored.elements.scheduledSyntaxDeny.click();assert.equal(restored.submitted.at(-1).approval_decision,'reject');
  restored.setApproval(null);restored.result('approval_decision',{approval_resolved:'exact-id',scheduled_syntax:{job_id:'denied',state:'rejected'}});await settle();
  assert.match(restored.elements.scheduledSyntaxStatus.textContent,/no job was queued or sent/);
  // Unconfirmed outcomes offer query-only recovery; terminal states offer no replay button.
  f.setJobs([job('unconfirmed')]);await e.scheduledSyntaxRefresh.click();
  assert.match(flatten(e.scheduledSyntaxQueue),/never reruns it/);
  await buttons(e.scheduledSyntaxQueue)[0].click();
  assert.equal(f.submitted.at(-1).mode,'scheduled_syntax_recover');assert.equal(f.submitted.at(-1).scheduled_job_id,'a'.repeat(32));
  f.setJobs([job('completed',{result:{evidence:{status:'pass',checks:['syntax only']}}}),job('expired'),job('cancelled'),job('failed')]);
  await e.scheduledSyntaxRefresh.click();assert.equal(buttons(e.scheduledSyntaxQueue).length,0);
  assert.match(flatten(e.scheduledSyntaxQueue),/Syntax evidence: pass/);assert.match(flatten(e.scheduledSyntaxQueue),/will not start/);
  // The live local check must identify each file and avoid remote-only protocol wording.
  f.setJobs([job('completed',{destination:'Dell',result:{
    evidence:{status:'failed',authorized:false,acceptance_scope:'Remote Python syntax evidence is not Windows or runtime acceptance.'},
    receipt:{checks:[{path:'valid.py',passed:true},{path:'invalid.py',passed:false}]}
  }})]);await e.scheduledSyntaxRefresh.click();
  const perFile=flatten(e.scheduledSyntaxQueue);
  assert.match(perFile,/valid\.py: syntax passed/);
  assert.match(perFile,/invalid\.py: syntax check failed/);
  assert.match(perFile,/without running your code/);
  assert.doesNotMatch(perFile,/Remote Python syntax|authorized|acceptance_scope/);
  f.setJobs([job('completed',{operation:'python-ruff-check-v1',result:{evidence:{status:'failed'},
    receipt:{operation:'python-ruff-check-v1',status:'findings',checks:[{path:'a.py',passed:false}],
      diagnostics:[{path:'a.py',line:1,column:5,code:'F821',message:'Undefined name <unsafe>'}]}}})]);
  await e.scheduledSyntaxRefresh.click();
  const lintEvidence=flatten(e.scheduledSyntaxQueue);
  assert.match(lintEvidence,/Lint evidence: failed/);assert.match(lintEvidence,/Tool result: findings/);
  assert.match(lintEvidence,/a.py:1:5 F821 Undefined name <unsafe>/);
  assert.match(lintEvidence,/does not execute your code or qualify a model reviewer/);
  f.setJobs([job('waiting',{expires_at:Math.floor(Date.now()/1000)-1})]);await e.scheduledSyntaxRefresh.click();
  assert(buttons(e.scheduledSyntaxQueue)[0].disabled);assert.match(flatten(e.scheduledSyntaxQueue),/deadline has passed/);
  // Validation and owner state suppress source requests.
  const before=f.submitted.length;f.contextProject({dirty:true});e.scheduledSyntaxFiles.value='a.py';await e.scheduledSyntaxPrepare.click();
  f.contextProject({dirty:false,stale:true});await e.scheduledSyntaxPrepare.click();
  f.contextProject({stale:false});e.scheduledSyntaxFiles.value='notes.txt';await e.scheduledSyntaxPrepare.click();
  e.scheduledSyntaxFiles.value=Array(9).fill('a.py').join('\n');await e.scheduledSyntaxPrepare.click();assert.equal(f.submitted.length,before);
  f.setApproval({kind:'remote_syntax'});await e.scheduledSyntaxRefresh.click();assert(e.scheduledSyntaxPrepare.disabled);
  assert.match(e.scheduledSyntaxStatus.textContent,/Another change/);
  // Polling is bounded, stops while hidden, and does not run alongside foreground work.
  f.setApproval(null);await e.scheduledSyntaxRefresh.click();const count=f.calls.length;
  f.busy(true);await f.timer();assert.equal(f.calls.length,count);f.busy(false);await f.timer();assert.equal(f.calls.length,count+2);
  f.visible(false);assert.equal(f.timers.size,0);const hiddenCount=f.calls.length;f.busy();assert.equal(f.calls.length,hiddenCount);
  f.visible(true);assert.equal(f.timers.size,1);await f.timer();assert.equal(f.calls.length,hiddenCount+2);
  // A late authenticated response cannot expose source or restore controls after lock.
  let resolveQueue,resolveApproval;
  f.context.api=route=>new Promise(resolve=>{if(route==='/api/syntax-queue')resolveQueue=resolve;else resolveApproval=resolve;});
  const reading=e.scheduledSyntaxRefresh.click();f.emit('prometheus-auth-lost');
  resolveQueue({jobs:[job()]});resolveApproval({approval:row()});await reading;
  assert(e.scheduledSyntaxReview.hidden && e.scheduledSyntaxApprove.disabled && e.scheduledSyntaxPrepare.disabled);
  assert.equal(e.scheduledSyntaxQueue.children.length,0);assert.equal(e.scheduledSyntaxFiles.value,'');assert.equal(f.timers.size,0);
  assert.match(e.scheduledSyntaxQueueStatus.textContent,/paused/);
  // Read failures are visible and retried on the bounded timer, not every app-status event.
  const disconnected=fixture();let reads=0;
  disconnected.context.api=async()=>{reads++;throw new Error('Queue unavailable');};
  disconnected.busy();await settle();assert.match(disconnected.elements.scheduledSyntaxQueueStatus.textContent,/not current.*Queue unavailable/);
  const failedReads=reads;disconnected.busy();disconnected.busy();await settle();assert.equal(reads,failedReads);
  await disconnected.timer();assert.equal(reads,failedReads+2);assert.equal(disconnected.submitted.length,0);
  // A fast result can finish before submitAction's promise returns to send().
  const synchronous=fixture();synchronous.setApproval(row());synchronous.busy();await settle();
  assert.match(flatten(synchronous.elements.scheduledSyntaxQueue),/No saved approved/);
  synchronous.context.submitAction=async body=>{
    assert.equal(body.mode,'approval_decision');synchronous.busy(true);
    synchronous.setApproval(null);synchronous.setJobs([job()]);
    synchronous.result('approval_decision',{approval_resolved:'exact-id',scheduled_syntax:job()});
    synchronous.busy(false);return true;
  };
  const priorReads=synchronous.calls.length;
  await synchronous.elements.scheduledSyntaxApprove.click();await settle();
  assert.equal(synchronous.calls.length,priorReads+2,'the completed action must refresh immediately, without waiting for the timer');
  assert.match(flatten(synchronous.elements.scheduledSyntaxQueue),/Surface · waiting/);
  console.log('Scheduled syntax UI: exact destination review, durable reload, cancellation, query-only recovery, bounded polling and lock races passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
