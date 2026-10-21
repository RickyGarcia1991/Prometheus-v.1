'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test'),{setup}=require('./test-workspace.cjs');
const code=fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/evidence-resolution.js'),'utf8');
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const target={id:'a'.repeat(32),fingerprint:'b'.repeat(64),evidence_conflict:{schema_version:1,state:'unresolved',read_only:true,freshness:'historical',requires_new_review:true,snapshot_sha256:'c'.repeat(64),source:'worker',variants:[{fact_sha256:'d'.repeat(64),status:'checks_failed',checks:{failed:1}},{fact_sha256:'e'.repeat(64),status:'checks_passed',checks:{passed:1}}]}};
const pending=()=>{
 const payload={target_id:target.id,target_fingerprint:target.fingerprint,source:'worker',reference:'3'.repeat(64),snapshot_sha256:target.evidence_conflict.snapshot_sha256,fact_sha256:'d'.repeat(64),selected_fact:{status:'checks_failed',checks:{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0},rollback:null,evidence_sha256:'4'.repeat(64)},reason:'Exact reason'};
 return {kind:'evidence_resolution',id:'f'.repeat(32),fingerprint:'1'.repeat(64),session:'2'.repeat(32),policy_revision:'policy-1',payload,review:{summary:'Record an owner interpretation of conflicting historical evidence.',effect:'Original observations and conflict warnings remain unchanged. No execution or retry is authorized.',...JSON.parse(JSON.stringify(payload))}};
};
function setupReview(){
 const f=setup(), events={};let history={schema_version:1,read_only:true,items:[target]}, live=null;
 f.context.document.hidden=false;f.context.document.addEventListener=(key,fn)=>events[key]=fn;
 f.context.session='2'.repeat(32);f.calls=[];
 f.context.api=async url=>{f.calls.push(url);return url==='/api/decisions/history'?(typeof history==='function'?history():history):{approval:live};};
 vm.runInNewContext(code,f.context);
 f.busy=(busy=false)=>f.emit('prometheus-busy',{online:true,busy,closed:false});
 f.open=async()=>{f.elements.evidenceResolution.open=true;f.elements.evidenceResolution.dispatch('toggle');await settle();};
 f.select=()=>{const e=f.elements;e.resolutionTarget.value=target.id;e.resolutionTarget.dispatch('change');e.resolutionFact.value='d'.repeat(64);e.resolutionReason.value='Exact reason';e.resolutionReason.dispatch('input');};
 f.setHistory=v=>history=v;f.setPending=v=>live=v;f.visibility=()=>events.visibilitychange();f.busy();return f;
}
const text=e=>[e.textContent,...e.children.map(text)].join('\n');
test('exact proposal and approval carry only reviewed identities and preserve selection changes',async()=>{
 const f=setupReview(),e=f.elements;await f.open();assert.equal(f.submitted.length,0);f.select();assert(!e.resolutionPrepare.disabled);
 await e.resolutionPrepare.click();assert.deepEqual(f.submitted.at(-1),{mode:'evidence_resolution_prepare',session:'2'.repeat(32),resolution_target_id:target.id,resolution_target_fingerprint:target.fingerprint,resolution_snapshot_sha256:'c'.repeat(64),resolution_fact_sha256:'d'.repeat(64),resolution_reason:'Exact reason'});
 f.busy(true);f.result('evidence_resolution_prepare',{approval:pending()},{origin:'local'});f.busy();
 assert.match(text(e.resolutionReview),/checks_failed/);assert.match(text(e.resolutionReview),/Exact reason/);
 e.resolutionFact.value='e'.repeat(64);await e.resolutionApprove.click();assert.deepEqual(f.submitted.at(-1),{mode:'approval_decision',session:'2'.repeat(32),approval_id:'f'.repeat(32),approval_fingerprint:'1'.repeat(64),approval_decision:'approve'});
 f.result('approval_decision',{approval_resolved:'f'.repeat(32)},{origin:'local',reply:'Interpretation recorded; originals unchanged.'});assert(e.resolutionReview.hidden);assert.match(e.resolutionStatus.textContent,/originals unchanged/);
});
test('restart refresh restores only live owner pending review and can reject',async()=>{
 const f=setupReview();f.setPending(pending());await f.open();await f.elements.resolutionReject.click();assert.equal(f.submitted.at(-1).approval_decision,'reject');
 f.result('approval_decision',{}, {origin:'local',error:'Evidence changed'});assert(f.elements.resolutionReview.hidden);assert.match(f.elements.resolutionStatus.textContent,/Evidence changed/);
});
test('new conversation result remains reviewable, observed results do not create authority',async()=>{
 const f=setupReview();f.context.session=null;await f.open();f.select();await f.elements.resolutionPrepare.click();
 f.context.session='2'.repeat(32);f.emit('prometheus-session-changed',{session:f.context.session});
 f.result('evidence_resolution_prepare',{approval:pending()},{origin:'observed'});assert(f.elements.resolutionReview.hidden);
 f.result('evidence_resolution_prepare',{approval:pending()},{origin:'local'});assert(!f.elements.resolutionReview.hidden);
 f.context.session='3'.repeat(32);f.emit('prometheus-session-changed',{session:f.context.session});assert(f.elements.resolutionReview.hidden);
});
test('late reads and results after hide, close or owner loss cannot recover controls',async()=>{
 for(const boundary of ['hide','close','auth','session']){
  const f=setupReview();let release;f.setHistory(()=>new Promise(resolve=>release=resolve));f.elements.evidenceResolution.open=true;f.elements.evidenceResolution.dispatch('toggle');await settle();
  if(boundary==='hide'){f.context.document.hidden=true;f.visibility();}
  if(boundary==='close'){f.elements.evidenceResolution.open=false;f.elements.evidenceResolution.dispatch('toggle');}
  if(boundary==='auth')f.emit('prometheus-auth-lost');
  if(boundary==='session'){f.context.session='3'.repeat(32);f.emit('prometheus-session-changed');}
  release({schema_version:1,read_only:true,items:[target]});await settle();assert.equal(f.elements.resolutionTarget.children.length,0);assert.equal(f.submitted.length,0);
 }
});
test('malformed conflict offers no action and historical records never submit jobs',async()=>{
 for(const bad of [{...target,evidence_conflict:null},{...target,id:'<script>'},{...target,evidence_conflict:{...target.evidence_conflict,read_only:false}}]){
  const f=setupReview();f.setHistory({schema_version:1,read_only:true,items:[bad]});await f.open();assert(f.elements.resolutionPrepare.disabled);assert.equal(f.submitted.length,0);
 }
});

const invalidReviews=[
 ['missing review',r=>delete r.review],['null review',r=>r.review=null],['empty review',r=>r.review={}],['text review',r=>r.review='approved'],
 ['missing payload',r=>delete r.payload],['wrong reason',r=>r.review.reason='Different reason'],['wrong fact',r=>r.review.selected_fact.status='checks_passed'],
 ['wrong digest',r=>r.review.fact_sha256='9'.repeat(64)],['wrong snapshot',r=>r.review.snapshot_sha256='9'.repeat(64)],
 ['wrong counts',r=>r.review.selected_fact.checks.failed=0],['extra authority',r=>r.review.execute=true],['extra payload',r=>r.payload.command='run'],
 ['extra fact field',r=>r.review.selected_fact.run=true],['wrong summary',r=>r.review.summary='Run this'],['wrong effect',r=>r.review.effect='Retry now'],
 ['missing policy',r=>delete r.policy_revision],['oversized policy',r=>r.policy_revision='x'.repeat(257)],['oversized reason',r=>{r.payload.reason=r.review.reason='x'.repeat(2001);}]
];
for(const [name,change] of invalidReviews)for(const path of ['refresh','local-result'])test('incomplete or mismatched '+name+' cannot enable decisions via '+path,async()=>{
 const f=setupReview(),r=pending();change(r);
 if(path==='refresh'){f.setPending(r);await f.open();}
 else{await f.open();f.select();await f.elements.resolutionPrepare.click();f.busy(true);f.result('evidence_resolution_prepare',{approval:r},{origin:'local'});f.busy();}
 assert(f.elements.resolutionApprove.disabled);assert(f.elements.resolutionReject.disabled);assert(f.elements.resolutionReview.hidden);
 assert.match(f.elements.resolutionStatus.textContent,/review unavailable/);
 const before=f.submitted.length;await f.elements.resolutionApprove.click();await f.elements.resolutionReject.click();assert.equal(f.submitted.length,before);
});
test('valid source shapes and reordered review fields remain reviewable',async()=>{
 for(const source of ['worker','project','application']){
  const f=setupReview(),r=pending();r.payload.source=source;
  if(source==='project')r.payload.selected_fact.rollback='restored';
  if(source==='project')r.payload.selected_fact.status='failed';
  if(source==='application')r.payload.selected_fact={status:'failed',checks:null,rollback:null,evidence_sha256:'4'.repeat(64)};
  r.review={effect:r.review.effect,summary:r.review.summary,...JSON.parse(JSON.stringify(r.payload))};
  f.setPending(r);await f.open();assert(!f.elements.resolutionApprove.disabled,source);assert(!f.elements.resolutionReject.disabled,source);
 }
});
