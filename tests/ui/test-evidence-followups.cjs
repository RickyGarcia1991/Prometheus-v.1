'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test'),{setup}=require('./test-workspace.cjs');
const assets=path.join(__dirname,'../../src/prometheus_assistant/ui_assets');
const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
const hex=(c,n=64)=>c.repeat(n),copy=x=>JSON.parse(JSON.stringify(x));
const fact=()=>({status:'checks_failed',checks:{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0},rollback:null,evidence_sha256:hex('f')});
const interpretation=()=>({target_id:hex('a',32),target_fingerprint:hex('b'),source:'worker',reference:hex('c'),snapshot_sha256:hex('d'),fact_sha256:hex('e'),selected_fact:fact(),reason:'Existing explanation'});
const item=()=>({interpretation_id:hex('1',32),interpretation_fingerprint:hex('2'),target_id:hex('a',32),target_fingerprint:hex('b'),source:'worker',reference:hex('c'),state_sha256:hex('3'),state:'active',effective_payload:interpretation(),effective_review_id:hex('1',32),effective_review_fingerprint:hex('2'),effective_change:null,currency:'matching_inputs',interpretation_conflict:true,history_conflict:true,total_changes:0,changes_truncated:false,changes:[],completion_unconfirmed:false});
const witness=()=>({observation_count:1,witnesses:[{id:hex('a',32),fingerprint:hex('b'),event_id:hex('c',32)}],witnesses_truncated:false});
const conflict=()=>({schema_version:1,read_only:true,state:'unresolved',freshness:'historical',requires_new_review:true,source:'worker',reference:hex('c'),snapshot_sha256:hex('d'),snapshot_scope:'collected_history_inputs',suggested_action:'review_worker_check',variant_count:2,observation_count:2,variants_truncated:false,variants:[{fact_sha256:hex('e'),...fact(),...witness()},{fact_sha256:hex('4'),status:'checks_passed',checks:{planned:1,executed:1,passed:1,failed:0,cancelled:0,timed_out:0},rollback:null,evidence_sha256:hex('5'),...witness()}]});
const history=()=>({schema_version:1,read_only:true,items:[{id:hex('a',32),fingerprint:hex('b'),interpretation_lifecycle:{schema_version:1,read_only:true,freshness:'historical',total:1,truncated:false,items:[item()]}}]});
const provenance=()=>({schema_version:1,interpretation_id:hex('1',32),interpretation_fingerprint:hex('2'),state_sha256:hex('3'),snapshot_sha256:hex('d'),fact_sha256:hex('e'),reason:'Check the selected saved code.',project:'C:/generated-project',interpretation:interpretation(),interpretation_review_id:hex('1',32),interpretation_review_fingerprint:hex('2'),source_kind:'worker',source_ref:hex('c'),conflict_targets:[{id:hex('a',32),fingerprint:hex('b')}],evidence_snapshot:conflict()});
function pending(){
 const target={account_id:'account',device_id:'surface',device_name:'Surface',worker_digest:hex('b'),configuration_digest:hex('c'),online:true};
 const request={schema_version:1,job_id:hex('9',32),device_id:'surface',worker_digest:hex('b'),operation:'python-syntax-check',timeout_seconds:10,files:[{path:'a.py',content:'answer = 42\n'}],request_digest:hex('a')};
 const placement={strategy:'remote-lightweight-v1',selected:'surface',reason:'Reserve Dell',candidates:[]};
 const followup=provenance();return {id:hex('6',32),fingerprint:hex('7'),session:hex('8',32),policy_revision:hex('9'),kind:'evidence_followup',payload:{request,target,placement,followup},review:{action:'Run check',reason:'Exact check',benefit:'New evidence',effects:'No source execution',duration:'One hour; one claim',destination:copy(target),placement:copy(placement),operation:request.operation,request_digest:request.request_digest,files:request.files.map(f=>({...f,sha256:hex('a'),utf8_bytes:12})),followup:copy(followup),resource_admission:{decision:'ready'}}};
}
function fixture(){
 const f=setup(),events={};let h=history(),p=null,jobs=[];f.context.session=hex('8',32);f.context.document.hidden=false;f.context.document.addEventListener=(name,fn)=>events[name]=fn;
 f.context.crypto=require('node:crypto').webcrypto;f.context.TextEncoder=require('node:util').TextEncoder;
 f.project={project:'C:/generated-project',dirty:false,stale:false};f.context.window.PrometheusWorkspace={context:()=>f.project};
 let assessment=null;f.assessmentRequests=[];f.setAssessment=value=>assessment=value;
 f.context.api=async (url,body)=>{if(url==='/api/evidence/followups/assess'){f.assessmentRequests.push(body);return typeof assessment==='function'?assessment():assessment;}return url==='/api/decisions/history'?(typeof h==='function'?h():h):url==='/api/evidence/followups'?{schema_version:1,read_only:true,jobs}:{approval:p};};
 vm.runInNewContext(fs.readFileSync(path.join(assets,'interpretation-lifecycle.js'),'utf8'),f.context);
 vm.runInNewContext(fs.readFileSync(path.join(assets,'evidence-followups.js'),'utf8'),f.context);
 f.busy=(busy=false)=>f.emit('prometheus-busy',{online:true,busy,closed:false});f.busy();
 f.open=async()=>{f.elements.evidenceFollowups.open=true;f.elements.evidenceFollowups.dispatch('toggle');await settle();};
 f.select=()=>{const e=f.elements;e.followupInterpretation.value=hex('1',32);e.followupInterpretation.dispatch('change');e.followupFiles.value='a.py';e.followupReason.value='Check the selected saved code.';e.followupReason.dispatch('input');};
 f.setHistory=v=>h=v;f.setPending=v=>p=v;f.setJobs=v=>jobs=v;f.hide=()=>{f.context.document.hidden=true;events.visibilitychange();};return f;
}
const text=e=>[e.textContent,...e.children.map(text)].join('\n');
function job(state='waiting',id=hex('9',32)){
 const p=provenance();delete p.project;delete p.interpretation;delete p.evidence_snapshot;
 p.project_sha256=hex('c');p.request_digest=hex('a');
 const j={job_id:id,state,operation:'python-syntax-check',files:['a.py'],destination:'Surface',device_id:'surface',worker_digest:hex('b'),reasons:[],result:null,approval_id:hex('6',32),fingerprint:hex('7'),decision:'approve',provenance:p,approved_at:100,expires_at:3700,next_attempt_at:130,placement:pending().payload.placement,tool:null,profile:null,queued:true};
 if(state==='completed')j.result={state,receipt:{schema_version:1,job_id:id,device_id:j.device_id,worker_digest:j.worker_digest,request_digest:p.request_digest,state,checks:[{path:'a.py',passed:true}]},evidence:{supplemental:true,authorized:false,request_digest:p.request_digest,status:'passed',reasons:[]}};
 return j;
}
function lintPending(){const r=pending();r.payload.request={...r.payload.request,schema_version:2,operation:'python-ruff-check-v1',tool:{sha256:hex('a'),version:'0.14.1'},profile:'ruff-python-v1'};r.review={...r.review,operation:r.payload.request.operation,tool:copy(r.payload.request.tool),profile:r.payload.request.profile};return r;}
test('fresh follow-up request binds history, evidence, files and check selection',async()=>{
 const f=fixture(),e=f.elements;await f.open();f.select();assert(!e.followupPrepare.disabled);await e.followupPrepare.click();
 assert.deepEqual(f.submitted.at(-1),{mode:'evidence_followup_prepare',session:hex('8',32),project:'C:/generated-project',remote_files:'a.py',followup_interpretation_id:hex('1',32),followup_interpretation_fingerprint:hex('2'),followup_state_sha256:hex('3'),followup_snapshot_sha256:hex('d'),followup_fact_sha256:hex('e'),followup_reason:'Check the selected saved code.',followup_operation:'python-syntax-check',followup_placement:'remote-lightweight-v1'});
 assert.match(text(e.followupInterpretationDetails),/Competing interpretations/);assert.match(text(e.followupInterpretationDetails),/Conflicting original/);
 f.busy(true);f.result('evidence_followup_prepare',{approval:pending()},{origin:'local'});f.busy();assert(!e.followupApprove.disabled);e.followupFiles.value='unreviewed.py';await e.followupApprove.click();assert.deepEqual(f.submitted.at(-1),{mode:'approval_decision',session:hex('8',32),approval_id:hex('6',32),approval_fingerprint:hex('7'),approval_decision:'approve'});
});
test('pending exact follow-up is reloaded and rejected without preparation',async()=>{const f=fixture();f.setPending(pending());await f.open();assert(!f.elements.followupReject.disabled);await f.elements.followupReject.click();assert.equal(f.submitted.at(-1).approval_decision,'reject');});
for(const [name,mutate] of [['wrong request digest',r=>r.review.request_digest=hex('f')],['different context',r=>r.review.followup.state_sha256=hex('f')],['missing target',r=>delete r.payload.target],['different source',r=>r.review.files[0].content='wrong'],['command injection',r=>r.payload.request.command='run'],['policy missing',r=>delete r.policy_revision],['wrong session',r=>r.session=hex('a',32)],['unmapped check',r=>r.payload.request.operation='shell'],['extra provenance',r=>r.payload.followup.claim=true],['different snapshot',r=>r.payload.followup.evidence_snapshot.snapshot_sha256=hex('f')]])test(name+' cannot enable follow-up approval',async()=>{const f=fixture(),r=pending();mutate(r);f.setPending(r);await f.open();assert(f.elements.followupApprove.disabled);assert(f.elements.followupReject.disabled);assert.equal(f.submitted.length,0);});
test('stale or malformed interpretations cannot request execution',async()=>{
 for(const change of [x=>x.currency='stale',x=>x.state='withdrawn',x=>x.state_sha256='bad']){const f=fixture(),h=history();change(h.items[0].interpretation_lifecycle.items[0]);f.setHistory(h);await f.open();f.select();assert(f.elements.followupPrepare.disabled);}
});
test('dirty or absent project and oversized reason stay disabled',async()=>{
 for(const project of [null,{project:'C:/p',dirty:true},{project:'C:/p',stale:true}]){const f=fixture();f.project=project;await f.open();f.select();assert(f.elements.followupPrepare.disabled);}
 const f=fixture();await f.open();f.select();f.elements.followupReason.value='😀'.repeat(2000);f.elements.followupReason.dispatch('input');assert(!f.elements.followupPrepare.disabled);f.elements.followupReason.value+='x';f.elements.followupReason.dispatch('input');assert(f.elements.followupPrepare.disabled);
});
test('historical incomplete changes remain visible without disabling a valid current head',async()=>{
 const f=fixture(),h=history(),x=h.items[0].interpretation_lifecycle.items[0];
 x.completion_unconfirmed=true;x.total_changes=2;
 const completed={approval_id:hex('6',32),fingerprint:hex('7'),operation:'revise',reason:x.effective_payload.reason,decision:'approve',recording_state:'recorded',created_at:2};
 x.effective_review_id=completed.approval_id;x.effective_review_fingerprint=completed.fingerprint;x.effective_change=completed;
 x.changes=[completed,{approval_id:hex('9',32),fingerprint:hex('a'),operation:'revise',reason:'Earlier incomplete attempt',decision:'approve',recording_state:'unrecorded',created_at:1}];
 f.setHistory(h);await f.open();f.select();assert(!f.elements.followupPrepare.disabled);assert.match(text(f.elements.followupInterpretationDetails),/earlier change has unconfirmed/);
});
test('late history after signout, tab hiding, close or session change stays inert',async()=>{
 for(const boundary of ['auth','hidden','close','session']){const f=fixture();let resolve;f.setHistory(()=>new Promise(r=>resolve=r));f.elements.evidenceFollowups.open=true;f.elements.evidenceFollowups.dispatch('toggle');await settle();if(boundary==='auth')f.emit('prometheus-auth-lost');if(boundary==='hidden')f.hide();if(boundary==='close'){f.elements.evidenceFollowups.open=false;f.elements.evidenceFollowups.dispatch('toggle');}if(boundary==='session'){f.context.session=hex('a',32);f.emit('prometheus-session-changed');}resolve(history());await settle();assert(f.elements.followupApprove.disabled);assert.equal(f.elements.followupInterpretation.children.length,0);}
});
test('waiting cancellation and uncertain recovery never prepare new work',async()=>{
 const f=fixture();f.setJobs([job(),job('unconfirmed',hex('a',32))]);await f.open();const cards=f.elements.followupJobs.children;await cards[0].children.find(x=>x.tagName==='BUTTON').click();assert.deepEqual(f.submitted.at(-1),{mode:'evidence_followup_cancel',followup_job_id:hex('9',32)});await cards[1].children.find(x=>x.tagName==='BUTTON').click();assert.deepEqual(f.submitted.at(-1),{mode:'evidence_followup_recover',followup_job_id:hex('a',32)});assert.match(text(f.elements.followupJobs),/never runs it again/);
});
test('all conflict variants and exact lint identity are required for review',async()=>{
 const good=fixture();good.setPending(lintPending());await good.open();assert(!good.elements.followupApprove.disabled);
 for(const mutate of [r=>r.payload.followup.evidence_snapshot.variants[1].checks.failed=-1,r=>r.payload.request.profile=r.review.profile='arbitrary',r=>r.payload.request.tool.version=r.review.tool.version='latest',r=>r.payload.request.tool.command=r.review.tool.command='run']){
  const f=fixture(),r=lintPending();mutate(r);r.review.followup=copy(r.payload.followup);f.setPending(r);await f.open();assert(f.elements.followupApprove.disabled);assert.equal(f.submitted.length,0);
 }
});
test('completed job receipts bind the exact job files and evidence',async()=>{
 const good=fixture();good.setJobs([job('completed')]);await good.open();assert.match(text(good.elements.followupJobs),/completed/);assert.doesNotMatch(text(good.elements.followupJobs),/unavailable/);
 for(const mutate of [j=>j.result.receipt.job_id=hex('f',32),j=>j.result.receipt.checks[0].path='other.py',j=>j.result.receipt.checks[0].passed=false,j=>j.result.evidence.authorized=true,j=>j.decision='reject',j=>j.state='success']){
  const f=fixture(),j=job('completed');mutate(j);f.setJobs([j]);await f.open();assert.match(f.elements.followupStatus.textContent,/unavailable/);assert.doesNotMatch(text(f.elements.followupJobs),/"status": "passed"/);assert.equal(f.submitted.length,0);
 }
});
test('nonqueued interrupted attempts cannot recover or execute',async()=>{const f=fixture(),j=job('unconfirmed');j.queued=false;f.setJobs([j]);await f.open();assert.match(text(f.elements.followupJobs),/not durably queued/);assert(!f.elements.followupJobs.children.flatMap(x=>x.children).some(x=>x.tagName==='BUTTON'));});

const canonicalResult=value=>Array.isArray(value)?'['+value.map(canonicalResult).join(',')+']':value&&typeof value==='object'?'{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonicalResult(value[k])).join(',')+'}':JSON.stringify(value).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
const assessmentFor=j=>({schema_version:1,read_only:true,scope:'point_in_time_inputs_only',job_id:j.job_id,approval_id:j.approval_id,fingerprint:j.fingerprint,request_digest:j.provenance.request_digest,result_sha256:require('node:crypto').createHash('sha256').update(canonicalResult(j.result)).digest('hex'),observed_at:100,historical:{state:j.state,result_state:j.result.state,check_status:j.result.evidence.status,terminal:true},applicability:'matching_inputs',checks:Object.fromEntries(['interpretation','evidence','source'].map(k=>[k,{status:'matching_inputs',reason:'matching_inputs'}])),authorizes_execution:false,resolves_conflicts:false,verifies_runtime_correctness:false});
const assessmentButton=f=>f.elements.followupJobs.children.flatMap(x=>x.children).find(x=>x.tagName==='BUTTON'&&x.textContent==='Check whether inputs still match');
test('assessment is explicit, read-only and bound to the selected saved result',async()=>{
 const f=fixture(),j=job('completed');f.setJobs([j]);f.setAssessment(assessmentFor(j));await f.open();
 assert.equal(f.assessmentRequests.length,0);const button=assessmentButton(f);assert(button);await button.click();
 assert.deepEqual(copy(f.assessmentRequests),[{job_id:j.job_id,project:f.project.project}]);
 assert.match(text(f.elements.followupJobs),/Inputs match at this observation/);assert.match(text(f.elements.followupJobs),/does not resolve/);assert.equal(f.submitted.length,0);
});
test('assessment rejects foreign result identity and stale response after signout',async()=>{
 const f=fixture(),j=job('completed');f.setJobs([j]);f.setAssessment({...assessmentFor(j),request_digest:hex('0')});await f.open();await assessmentButton(f).click();assert.match(text(f.elements.followupJobs),/Assessment unavailable/);
 const g=fixture();g.setJobs([j]);let finish;g.setAssessment(()=>new Promise(r=>finish=r));await g.open();const pending=assessmentButton(g).click();await settle();g.emit('prometheus-auth-lost');finish(assessmentFor(j));await pending;assert.equal(g.elements.followupJobs.children.length,0);
});
test('matching inputs never hides a failed historical check',async()=>{
 const f=fixture(),j=job('completed');j.result.receipt.checks[0].passed=false;j.result.evidence.status='failed';f.setJobs([j]);f.setAssessment(assessmentFor(j));await f.open();await assessmentButton(f).click();
 assert.match(text(f.elements.followupJobs),/Historical check: failed/);assert.match(text(f.elements.followupJobs),/Inputs match at this observation/);assert.equal(f.submitted.length,0);
});
test('fresh review navigation reloads current history and copies no approval authority',async()=>{
 const f=fixture(),j=job('completed');f.setJobs([j]);f.setAssessment(assessmentFor(j));await f.open();await assessmentButton(f).click();
 const all=e=>[e,...e.children.flatMap(all)];const fresh=all(f.elements.followupJobs).find(x=>x.textContent==='Start a fresh follow-up review');assert(fresh);await fresh.click();
 assert.equal(f.elements.followupFiles.value,'a.py');assert.equal(f.elements.followupReason.value,'');assert(f.elements.followupPrepare.disabled);assert.equal(f.submitted.length,0);assert.match(f.elements.followupStatus.textContent,/Nothing has been approved or run/);
});
test('changed project or hidden page discards an assessment response',async()=>{
 for(const boundary of ['project','hidden','session']){const f=fixture(),j=job('completed');f.setJobs([j]);let finish;f.setAssessment(()=>new Promise(r=>finish=r));await f.open();const pending=assessmentButton(f).click();await settle();if(boundary==='project')f.project={project:'C:/different',dirty:false,stale:false};if(boundary==='hidden')f.hide();if(boundary==='session'){f.context.session=hex('a',32);f.emit('prometheus-session-changed');}finish(assessmentFor(j));await pending;assert.doesNotMatch(text(f.elements.followupJobs),/Inputs match at this observation/);}
});
test('dirty projects never start assessment and malformed positive claims are rejected',async()=>{
 const dirty=fixture();dirty.setJobs([job('completed')]);await dirty.open();dirty.project.dirty=true;await assessmentButton(dirty).click();assert.equal(dirty.assessmentRequests.length,0);
 for(const mutate of [a=>a.authorizes_execution=true,a=>a.historical.check_status='failed',a=>a.checks.source.status='stale',a=>a.result_sha256='bad',a=>a.observed_at=-1]){const f=fixture(),j=job('completed'),a=assessmentFor(j);mutate(a);f.setJobs([j]);f.setAssessment(a);await f.open();await assessmentButton(f).click();assert.match(text(f.elements.followupJobs),/Assessment unavailable/);assert.equal(f.submitted.length,0);}
});
test('follow-up history validates provenance against each parent without new authority',()=>{
 const f=fixture(),j=job('completed'),p={schema_version:1,read_only:true,freshness:'historical',items:[j],total:1,truncated:false};
 for(const [id,fp] of [[j.approval_id,j.fingerprint],[j.provenance.interpretation_id,j.provenance.interpretation_fingerprint],[j.provenance.interpretation_review_id,j.provenance.interpretation_review_fingerprint],[hex('a',32),hex('b')]]){const el=f.context.document.createElement('li');f.context.window.PrometheusEvidenceFollowups.renderHistory(el,{id,fingerprint:fp,evidence_followups:p});assert.match(text(el),/Supplemental check evidence: passed/);}
 for(const row of [{id:hex('f',32),fingerprint:hex('f'),evidence_followups:p},{id:j.approval_id,fingerprint:j.fingerprint,evidence_followups:{...p,items:[{...j,state:'invented'}]}}]){const el=f.context.document.createElement('li');f.context.window.PrometheusEvidenceFollowups.renderHistory(el,row);assert.match(text(el),/history unavailable/);assert.doesNotMatch(text(el),/Supplemental check evidence: passed/);}
 assert.equal(f.submitted.length,0);
});
