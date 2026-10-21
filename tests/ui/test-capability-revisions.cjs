'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test'),{setup}=require('./test-workspace.cjs'),{fixture:appFixture,settle}=require('./test-app.cjs');
const source=fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/capability-revisions.js'),'utf8');
const conversation='a'.repeat(32),token='b'.repeat(32);
const approval=(kind='runtime_config')=>({kind,id:'c'.repeat(32),fingerprint:'d'.repeat(64),session:conversation,review:{reason:'<img src=x onerror=bad>',proposed_backend:'cpu'}});
const flatten=el=>[el.textContent,...el.children.map(flatten)].join(' ');
function fixture(){
 const f=setup({runWorkspace:false}),events={},calls=[];let response={approval:approval()},serial=0;
 f.context.session=conversation;f.context.document.hidden=false;f.context.document.addEventListener=(name,handler)=>events[name]=handler;
 f.context.api=async url=>{calls.push(url);return typeof response==='function'?response():response;};
 f.context.localStorage={setItem(){throw Error('Revision context must not be stored.');}};
 vm.runInNewContext(source,f.context);
 const helper=f.context.window.PrometheusCapabilityRevisions;
 f.context.submitAction=async body=>{const request=helper.withContext(body);f.submitted.push(request);helper.accepted(request,'job-'+(++serial));return true;};
 f.emit('prometheus-busy',{online:true,busy:false,closed:false});
 const open=async()=>{f.elements.capabilityRevision.open=true;f.elements.capabilityRevision.dispatch('toggle');await settle();};
 const revisionResult=(overrides={},extra={})=>f.result('approval_decision',{approval_resolved:approval().id,capability_revision:{kind:'runtime_config',origin_approval:approval().id,session:conversation,feedback:'Use less memory',capability_revision_token:token,...overrides}},{origin:'local',job_id:'job-'+serial,...extra});
 return {...f,calls,helper,open,revisionResult,set:value=>response=value,events,submit:body=>f.context.submitAction(body)};
}
async function requested(kind='runtime_config'){
 const f=fixture();f.set({approval:approval(kind)});await f.open();f.elements.capabilityRevisionFeedback.value='Use less memory';await f.elements.capabilityRevisionSubmit.click();return f;
}
test('explicit pending refresh displays literal proposal and exact request_changes without preparing or executing',async()=>{
 const f=fixture();assert.equal(f.calls.length,0);await f.open();
 assert.match(flatten(f.elements.capabilityRevisionProposal),/<img src=x onerror=bad>/);
 f.elements.capabilityRevisionFeedback.value='  Use less memory  ';await f.elements.capabilityRevisionSubmit.click();
 assert.deepEqual(JSON.parse(JSON.stringify(f.submitted[0])),{mode:'approval_decision',approval_decision:'request_changes',approval_id:approval().id,approval_fingerprint:approval().fingerprint,session:conversation,approval_feedback:'Use less memory'});
 f.revisionResult();assert.equal(f.submitted.length,1);assert.match(f.elements.capabilityRevisionStatus.textContent,/explicitly prepare.*fresh approval/);
 assert.equal(f.elements.capabilityRevisionProposal.hidden,true);
});
test('all six prepare modes attach only a matching one-use context',async()=>{
 for(const [kind,mode] of [['runtime_config','runtime_config'],['idle_config','idle_config'],['integration','integration'],['integration_access','integration_access'],['remote_syntax','remote_syntax_prepare'],['scheduled_syntax','scheduled_syntax_prepare']]){
  const f=await requested(kind);f.revisionResult({kind});
  const request=f.helper.withContext({mode,session:conversation,capability_revision_token:'untrusted'});
  assert.equal(request.capability_revision_token,token,kind);
  assert.throws(()=>f.helper.withContext({mode,session:conversation}),/Wait for the current replacement/);
  f.helper.accepted(request,'replacement');
  assert.equal(f.helper.withContext({mode,session:conversation}).capability_revision_token,undefined,'consumed only after accepted prepare');
 }
});
test('status, decision, wrong kind and wrong session cannot inherit revision context',async()=>{
 const f=await requested();f.revisionResult();
 for(const body of [{mode:'runtime_status',session:conversation},{mode:'approval_decision',session:conversation},{mode:'integration',session:conversation},{mode:'runtime_config',session:'e'.repeat(32)},{mode:'runtime_config'}])assert.equal(f.helper.withContext({...body,capability_revision_token:'untrusted'}).capability_revision_token,undefined);
 assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,token);
});
test('observed, uncorrelated and already consumed result events cannot import live context',async()=>{
 for(const extra of [{origin:'observed'},{origin:undefined},{job_id:'other-job'}]){
  const f=await requested();f.revisionResult({},extra);assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
 }
 const f=await requested();f.revisionResult();const consumed=f.helper.withContext({mode:'runtime_config',session:conversation});f.helper.accepted(consumed,'replacement');f.revisionResult();assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
 const g=await requested();await g.submit({mode:'runtime_status',session:conversation});g.revisionResult({}, {job_id:'job-1'});assert.equal(g.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
});
test('context must match the exact request, proposal, feedback, kind and conversation',async()=>{
 for(const patch of [{kind:'integration'},{origin_approval:'e'.repeat(32)},{session:'e'.repeat(32)},{feedback:'Other changes'},{capability_revision_token:'<script>'}]){
  const f=await requested();f.revisionResult(patch);assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
 }
 const f=fixture();await f.open();await f.submit({mode:'approval_decision',approval_id:approval().id,approval_fingerprint:'e'.repeat(64),session:conversation,approval_decision:'request_changes',approval_feedback:'Use less memory'});f.revisionResult();assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
 const g=fixture();await g.open();await g.submit({mode:'approval_decision',approval_id:approval().id,approval_fingerprint:approval().fingerprint,session:conversation,approval_decision:'approve',approval_feedback:'Use less memory'});g.revisionResult();assert.equal(g.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
});
test('auth loss, session change, restart and reconnect clear tokens and delayed acknowledgments',async()=>{
 for(const event of ['prometheus-auth-lost','prometheus-session-changed','prometheus-restart','offline']){
  const f=await requested();f.revisionResult();const ticket=f.helper.withContext({mode:'approval_decision',session:conversation});
  if(event==='offline')f.emit('prometheus-busy',{online:false});else f.emit(event);
  f.helper.accepted(ticket,'stale-job');f.revisionResult({}, {job_id:'stale-job'});
  assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined,event);
  assert.equal(f.elements.capabilityRevisionProposal.hidden,true);
 }
 const f=await requested();f.revisionResult();f.context.session='e'.repeat(32);assert.equal(f.helper.withContext({mode:'runtime_config',session:f.context.session}).capability_revision_token,undefined);
 const delayed=fixture();await delayed.open();const request=delayed.helper.withContext({mode:'approval_decision',approval_id:approval().id,approval_fingerprint:approval().fingerprint,session:conversation,approval_decision:'request_changes',approval_feedback:'Use less memory'});delayed.emit('prometheus-auth-lost');delayed.helper.accepted(request,'delayed-ack');delayed.emit('prometheus-busy',{online:true,closed:false});delayed.revisionResult({}, {job_id:'delayed-ack'});assert.equal(delayed.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined,'post-auth acknowledgment cannot regain authority');
 const fresh=fixture();assert.equal(fresh.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined,'reload has no saved context');
});
test('history or pending API data cannot restore revision context',async()=>{
 const f=fixture();f.set({approval:approval(),capability_revision:{kind:'runtime_config',capability_revision_token:token,session:conversation}});await f.open();
 f.result('history',{capability_revision:{kind:'runtime_config',capability_revision_token:token,session:conversation}},{origin:'local',job_id:'unrequested'});
 assert.equal(f.helper.withContext({mode:'runtime_config',session:conversation}).capability_revision_token,undefined);
});
test('feedback limits and unsupported or other-conversation pending proposals cannot submit',async()=>{
 for(const value of ['', 'x'.repeat(2001),'contains\0nul']){const f=fixture();await f.open();f.elements.capabilityRevisionFeedback.value=value;await f.elements.capabilityRevisionSubmit.click();assert.equal(f.submitted.length,0);}
 for(const value of [approval('project_edit'),{...approval(),session:'e'.repeat(32)},{...approval(),fingerprint:'invalid'}]){const f=fixture();f.set({approval:value});await f.open();assert(f.elements.capabilityRevisionSubmit.disabled);}
});
test('late pending reads cannot restore an old session or override a newer local request',async()=>{
 const f=fixture();let resolve;f.set(()=>new Promise(r=>resolve=r));void f.open();await settle();f.emit('prometheus-session-changed');resolve({approval:approval()});await settle();assert(f.elements.capabilityRevisionSubmit.disabled);
 const g=fixture();let finish;g.set(()=>new Promise(r=>finish=r));void g.open();await settle();await g.submit({mode:'integration',session:conversation});g.result('integration',{approval:approval('integration')},{origin:'local',job_id:'job-1'});finish({approval:approval()});await settle();assert.match(flatten(g.elements.capabilityRevisionProposal),/Integration action/);assert.doesNotMatch(flatten(g.elements.capabilityRevisionProposal),/Runtime settings/);
});
test('central app submit hook binds real job identity, session and one-use replacement',async()=>{
 const f=await appFixture();f.run(source);f.run('rememberSession('+JSON.stringify(conversation)+');setBusy(false)');
 f.queued.set('/api/approvals/pending',[Promise.resolve({ok:true,status:200,json:async()=>({approval:approval()})})]);
 f.elements.capabilityRevision.open=true;f.elements.capabilityRevision.dispatch('toggle');await settle();
 f.elements.capabilityRevisionFeedback.value='Use less memory';await f.elements.capabilityRevisionSubmit.click();await settle();const id=f.run('job');
 f.jobs.set(id,{state:'done',action:'approval_decision',result:{action:'approval_decision',session:conversation,details:{approval_resolved:approval().id,capability_revision:{kind:'runtime_config',origin_approval:approval().id,session:conversation,feedback:'Use less memory',capability_revision_token:token}}}});
 await f.run('pollJob('+JSON.stringify(id)+')');await settle();
 assert.equal(f.requests.filter(row=>row.url==='/api/jobs').length,1,'result does not prepare automatically');
 f.queued.set('/api/jobs',[Promise.resolve({ok:false,status:400,json:async()=>({error:'Correct the inputs.'})})]);
 assert.equal(await f.run('submitAction('+JSON.stringify({mode:'runtime_config',session:conversation,runtime_backend:'invalid'})+')'),false);
 assert.match(f.elements.capabilityRevisionStatus.textContent,/link.*retained/);
 await f.run('submitAction('+JSON.stringify({mode:'runtime_config',session:conversation,runtime_backend:'cpu'})+')');
 assert.equal(f.requests.filter(row=>row.url==='/api/jobs').at(-1).body.capability_revision_token,token);
 assert(!f.storageWrites.some(row=>String(row.value).includes(token)),'live token never enters local storage');
 assert(!flatten(f.elements.messages).includes(token),'live token is omitted from displayed result details');
 f.run('rememberSession('+JSON.stringify('e'.repeat(32))+')');assert(f.elements.capabilityRevisionSubmit.disabled);
});

test('a revised integration removes continuing grants and retains context only on definite rejection',async()=>{
 const f=await requested('integration');f.revisionResult({kind:'integration'});
 const first=f.helper.withContext({mode:'integration',session:conversation,task_grant:'f'.repeat(48)});
 assert.equal(first.task_grant,undefined);assert.equal(first.capability_revision_token,token);
 f.helper.failed(first,{status:400});
 const retry=f.helper.withContext({mode:'integration',session:conversation});
 assert.equal(retry.capability_revision_token,token);
 f.helper.failed(retry,new Error('network lost'));
 assert.equal(f.helper.withContext({mode:'integration',session:conversation}).capability_revision_token,undefined);
});
