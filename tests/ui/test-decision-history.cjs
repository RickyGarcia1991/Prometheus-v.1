'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');const {setup}=require('./test-workspace.cjs');
const script=fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/decision-history.js'),'utf8');
const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
const row={id:'a',fingerprint:'b',session:'c',kind:'project_edit',decision:'request_changes',feedback:'<img onerror=bad>',decided_at:1};
function fixture(){const f=setup(),calls=[],events={};let response={schema_version:1,read_only:true,items:[row]};f.context.document.hidden=false;f.context.document.addEventListener=(k,fn)=>events[k]=fn;f.context.api=async(...args)=>{calls.push(args);if(response instanceof Error)throw response;return typeof response==='function'?response():response;};vm.runInNewContext(script,f.context);return {...f,calls,set:r=>response=r,busy:(busy=false,online=true)=>f.emit('prometheus-busy',{busy,online,closed:false}),open:()=>{f.elements.decisionHistory.open=true;f.elements.decisionHistory.dispatch('toggle');},hide:()=>{f.context.document.hidden=true;events.visibilitychange();}};}
test('only explicit open/refresh reads; literal feedback; no history actions',async()=>{const f=fixture();f.busy();await settle();assert.equal(f.calls.length,0);f.open();await settle();assert.deepEqual(f.calls,[['/api/decisions/history']]);const list=f.elements.decisionHistoryList;assert.equal(list.children[0].children[1].textContent,row.feedback);assert.equal(list.children[0].children.length,3);assert.match(f.elements.decisionHistoryStatus.textContent,/does not authorize/);f.busy();await settle();assert.equal(f.calls.length,1);assert.equal(f.submitted.length,0);});
test('auth or hidden invalidates pending response and clears retained rows',async()=>{for(const fault of ['auth','hidden','busy']){const f=fixture();let resolve;f.set(()=>new Promise(r=>resolve=r));f.busy();f.open();await settle();if(fault==='auth')f.emit('prometheus-auth-lost');else if(fault==='hidden')f.hide();else f.busy(true);resolve({schema_version:1,read_only:true,items:[row]});await settle();assert.equal(f.elements.decisionHistoryList.children.length,0);}});
test('malformed failed and oversized responses show unavailable not empty success',async()=>{for(const value of [new Error('network'),{}, {schema_version:1,read_only:true,items:Array(51).fill(row)},{schema_version:1,read_only:true,items:[{...row,decision:'execute'}]}]){const f=fixture();f.set(value);f.busy();f.open();await settle();assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);}});
test('unknown kind uses generic label; receipts cannot navigate or authorize',async()=>{const f=fixture();f.set({schema_version:1,read_only:true,items:[{...row,kind:'<private-kind>'}]});f.busy();f.open();await settle();assert.match(f.elements.decisionHistoryList.children[0].children[0].textContent,/Owner decision/);assert.equal(f.submitted.length,0);});
const flatten=el=>[el.textContent,...el.children.map(flatten)].join(' ');
const outcome=(phase,status,extra={})=>({event_id:(phase==='started'?'1':'2').repeat(32),phase,status,observed_at:2,project_hash:'a'.repeat(64),edit_id:phase==='started'||status==='unknown'?'':'e'.repeat(32),checks:null,rollback:'unknown',evidence_sha256:phase==='result'&&status!=='unknown'||phase==='undo_result'?'b'.repeat(64):null,...extra});
async function history(events,decision='approve'){const f=fixture();f.set({schema_version:1,read_only:true,items:[{...row,decision,outcomes:events}]});f.busy();f.open();await settle();return f;}
test('failed native checks remain failed beside restored rollback',async()=>{const f=await history([outcome('started','started'),outcome('result','failed',{rollback:'restored',checks:{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0}})]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,/Execution failed/);assert.match(text,/Rollback: original content restored/);assert.match(text,/failed 1/);assert.equal(f.submitted.length,0);});
test('missing execution and interrupted execution never imply success',async()=>{for(const events of [undefined,[],[outcome('started','started')]]){const f=await history(events);assert.match(flatten(f.elements.decisionHistoryList),events?.length?/outcome unconfirmed.*not automatically resumed/:/No linked execution evidence/);assert.equal(f.submitted.length,0);}});
test('manual undo uncertainty and failure retain original outcome',async()=>{const original=[outcome('started','started'),outcome('result','failed')];const start=outcome('undo_started','started',{event_id:'3'.repeat(32)});let f=await history([...original,start]);assert.match(flatten(f.elements.decisionHistoryList),/Execution failed.*Restoration unconfirmed/);f=await history([...original,start,outcome('undo_result','restore_failed',{event_id:'4'.repeat(32),rollback:'needs_attention'})]);assert.match(flatten(f.elements.decisionHistoryList),/Execution failed.*Manual restoration failed/);});
test('no checks are not behavioral pass; cancelled and unchanged stay literal',async()=>{for(const status of ['applied_without_checks','cancelled','unchanged','unknown']){const f=await history([outcome('started','started'),outcome('result',status)]);assert.match(flatten(f.elements.decisionHistoryList),/behavior is unverified/);assert.doesNotMatch(flatten(f.elements.decisionHistoryList),/Native checks passed/);}});
test('malformed outcome and sequence fails closed',async()=>{for(const events of [null,{},Array(17).fill(outcome('started','started')),[outcome('result','passed')],[outcome('started','<img>')],[outcome('started','started',{checks:'bad'})],[outcome('started','started',{project_hash:'<img>'})],[outcome('started','started',{observed_at:NaN})],[outcome('started','started',{checks:{planned:true,executed:0,passed:0,failed:0,cancelled:0,timed_out:0}})]]){const f=await history(events);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);}});
test('denied and requested changes cannot carry execution authority',async()=>{for(const decision of ['reject','request_changes']){let f=await history([],decision);assert.match(flatten(f.elements.decisionHistoryList),/authorized no execution/);f=await history([outcome('started','started')],decision);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);}});
test('disconnected late outcome read cannot reappear after refresh',async()=>{const f=fixture();let resolve;f.set(()=>new Promise(r=>resolve=r));f.busy();f.open();await settle();f.busy(false,false);f.busy();resolve({schema_version:1,read_only:true,items:[{...row,decision:'approve',outcomes:[outcome('started','started')]}]});await settle();assert.equal(f.elements.decisionHistoryList.children.length,0);});

test('passed requires complete nonzero successful native counts',async()=>{for(const checks of [null,{planned:0,executed:0,passed:0,failed:0,cancelled:0,timed_out:0},{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0}]){const f=await history([outcome('started','started'),outcome('result','passed',{checks})]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);}const f=await history([outcome('started','started'),outcome('result','passed',{checks:{planned:1,executed:1,passed:1,failed:0,cancelled:0,timed_out:0},rollback:'not_attempted'})]);assert.match(flatten(f.elements.decisionHistoryList),/Native checks passed/);});

const capability=(status,extra={})=>({event_id:(status==='started'?'1':'2').repeat(32),phase:status==='started'?'started':'observed',observed_at:2,status,job_id:'',request_digest:'',checks:null,evidence_sha256:['started','unknown'].includes(status)?null:'b'.repeat(64),...extra});
const jobBinding={job_id:'c'.repeat(32),request_digest:'d'.repeat(64)};
async function capabilityHistory(kind,events,extra={}){const f=fixture();f.set({schema_version:1,read_only:true,items:[{...row,decision:'approve',kind,capability_outcomes:events,...extra}]});f.busy();f.open();await settle();return f;}
test('capability settings access and provider return remain distinct',async()=>{
 for(const [kind,status,label] of [['runtime_config','settings_saved',/Settings saved; future behavior has not been verified/],['idle_config','settings_saved',/Settings saved/],['integration_access','access_granted',/Access issued; this does not confirm a provider action/],['integration','adapter_returned',/adapter returned; result correctness is not independently verified/]]){
  const f=await capabilityHistory(kind,[capability('started'),capability(status)]);assert.match(flatten(f.elements.decisionHistoryList),label);assert.doesNotMatch(flatten(f.elements.decisionHistoryList),/checks passed/);assert.deepEqual(f.calls,[['/api/decisions/history']]);assert.equal(f.submitted.length,0);
 }
});
test('async history retains waiting uncertainty and verified failures without replay',async()=>{
 const start=capability('started',jobBinding),waiting=capability('waiting',jobBinding),uncertain=capability('unconfirmed',{...jobBinding,event_id:'3'.repeat(32)}),failed=capability('checks_failed',{...jobBinding,event_id:'4'.repeat(32),checks:{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0}});
 const f=await capabilityHistory('scheduled_syntax',[start,waiting,uncertain,failed]);assert.match(flatten(f.elements.decisionHistoryList),/Waiting at the approved destination.*Execution unconfirmed.*Native worker checks found failures/);assert.equal(f.submitted.length,0);
 for(const status of ['tool_failed','cancelled','expired','deferred']){const g=await capabilityHistory('remote_syntax',[start,capability(status,jobBinding)]);assert.doesNotMatch(g.elements.decisionHistoryStatus.textContent,/unavailable/);assert.doesNotMatch(flatten(g.elements.decisionHistoryList),/Native worker checks passed/);}
});
test('capability schemas and contradictory success fail closed',async()=>{
 const start=capability('started',jobBinding),pass=capability('checks_passed',{...jobBinding,checks:{planned:1,executed:1,passed:1,failed:0,cancelled:0,timed_out:0}});
 for(const rows of [[pass],[start,{...pass,checks:null}],[start,{...pass,checks:{...pass.checks,failed:1}}],[start,{...pass,request_digest:'f'.repeat(64)}],[start,{...pass,evidence_sha256:null}],[start,{...pass,private:'secret'}],[start,pass,capability('unknown',{...jobBinding,event_id:'3'.repeat(32)})],[start,capability('settings_saved',jobBinding)],null]){const f=await capabilityHistory('scheduled_syntax',rows);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);}
 const valid=await capabilityHistory('scheduled_syntax',[start,pass]);assert.match(flatten(valid.elements.decisionHistoryList),/Native worker checks passed/);
 for(const extra of [{decision:'reject'},{decision:'request_changes'},{outcomes:[outcome('started','started')]}]){const f=await capabilityHistory('scheduled_syntax',[start],extra);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);}
 for(const kind of ['project_edit','self_improve','unsupported']){const f=await capabilityHistory(kind,[capability('started')]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);}
});
test('incomplete capability start and missing legacy observations stay unconfirmed',async()=>{const f=await capabilityHistory('integration',[capability('started')]);assert.match(flatten(f.elements.decisionHistoryList),/outcome unconfirmed.*cannot resume or repeat/);const g=await capabilityHistory('integration',undefined);assert.match(flatten(g.elements.decisionHistoryList),/No linked execution evidence/);});
const revisionLink={parent_id:'a'.repeat(32),parent_fingerprint:'b'.repeat(64),child_id:'c'.repeat(32),child_fingerprint:'d'.repeat(64),child_state:'recorded',created_at:3};
async function revisionHistory(items){const f=fixture();f.set({schema_version:1,read_only:true,items});f.busy();f.open();await settle();return f;}
const revisionParent={...row,id:revisionLink.parent_id,fingerprint:revisionLink.parent_fingerprint,revisions:[revisionLink]};
test('revision chain shows separate decisions and retains unknown replacement authority',async()=>{
 const f=await revisionHistory([revisionParent,{...row,id:revisionLink.child_id,fingerprint:revisionLink.child_fingerprint,decision:'approve',revisions:[revisionLink]}]);
 assert.match(flatten(f.elements.decisionHistoryList),/Replacement review cccccccc.*separate decision.*Replacement review for requested changes in review aaaaaaaa.*own decision/);
 assert.equal(f.submitted.length,0);assert.deepEqual(f.calls,[['/api/decisions/history']]);
 const g=await revisionHistory([{...revisionParent,revisions:[{...revisionLink,child_state:'unrecorded'}]}]);
 assert.match(flatten(g.elements.decisionHistoryList),/no decision is recorded.*not restored/);
});
test('untrusted revision relationships fail closed',async()=>{
 for(const revisions of [null,[{...revisionLink,parent_id:'<img>'}],[{...revisionLink,parent_fingerprint:'f'.repeat(64)}],[{...revisionLink,child_id:revisionLink.parent_id}],[{...revisionLink,created_at:Infinity}],[{...revisionLink,child_state:'approved'}],[{...revisionLink,private:'hidden'}],Array(17).fill(revisionLink),[revisionLink,revisionLink]]){
  const f=await revisionHistory([{...revisionParent,revisions}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);
 }
 for(const extra of [{decision:'approve'},{kind:'runtime_config'}]){const f=await revisionHistory([{...revisionParent,...extra}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);}
});
const capabilityLink={...revisionLink,kind:'runtime_config'};
const capabilityParent={...revisionParent,kind:'runtime_config',revisions:[],capability_revisions:[capabilityLink]};
test('capability revision chains show kind and separate recorded or unknown child decisions',async()=>{
 for(const kind of ['runtime_config','idle_config','integration','integration_access','remote_syntax','scheduled_syntax']){
  const link={...capabilityLink,kind};
  const f=await revisionHistory([{...capabilityParent,kind,capability_revisions:[link]},{...row,id:link.child_id,fingerprint:link.child_fingerprint,kind,decision:'reject',capability_revisions:[link]}]);
  assert.match(flatten(f.elements.decisionHistoryList),/revision chain.*separate decision.*own decision/);assert.equal(f.submitted.length,0);
  const g=await revisionHistory([{...capabilityParent,kind,capability_revisions:[{...link,child_state:'unrecorded'}]}]);assert.match(flatten(g.elements.decisionHistoryList),/no decision is recorded.*not restored/);
 }
});
test('capability links reject mixed kinds, malformed identities and restored historical authority',async()=>{
 for(const links of [null,[{...capabilityLink,kind:'project_edit'}],[{...capabilityLink,kind:'integration'}],[{...capabilityLink,parent_id:'e'.repeat(32)}],[{...capabilityLink,child_state:'approved'}],[{...capabilityLink,capability_revision_token:'a'.repeat(32)}],[capabilityLink,capabilityLink]]){
  const f=await revisionHistory([{...capabilityParent,capability_revisions:links}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);
 }
 const f=await revisionHistory([{...capabilityParent,decision:'approve'}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);
 const g=await revisionHistory([{...capabilityParent,id:capabilityLink.child_id,fingerprint:capabilityLink.child_fingerprint,capability_revisions:[{...capabilityLink,child_state:'unrecorded'}]}]);assert.match(g.elements.decisionHistoryStatus.textContent,/unavailable/);
});
const sharedGroup={reference:'e'.repeat(64),kind:'worker_result',status:'checks_failed',linked_decisions:2,related_decisions:[{id:'f'.repeat(32),fingerprint:'f'.repeat(64)}],truncated:false,read_only:true};
const sharedRow={...row,id:'a'.repeat(32),fingerprint:'b'.repeat(64),kind:'scheduled_syntax',decision:'approve',capability_outcomes:[capability('started',jobBinding),capability('checks_failed',{...jobBinding,checks:{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0}})],shared_evidence:[sharedGroup]};
test('shared native evidence is readonly provenance and never a separate run or replay button',async()=>{
 const f=await revisionHistory([sharedRow]);const text=flatten(f.elements.decisionHistoryList);
 assert.match(text,/Shared native worker observation.*checks found failures/);assert.match(text,/2 decisions link to this same recorded result/);assert.match(text,/Read-only provenance.*do not establish separate executions.*replay/);assert(text.includes(sharedGroup.reference));
 assert.equal(f.submitted.length,0);assert.deepEqual(f.calls,[['/api/decisions/history']]);
 function assertDisplayOnly(node){assert(!['BUTTON','A','INPUT'].includes(node.tagName));node.children.forEach(assertDisplayOnly);}assertDisplayOnly(f.elements.decisionHistoryList);
});
test('shared evidence preserves tool failure and validates bounded truncation',async()=>{
 const related=Array.from({length:16},(_,i)=>({id:(i+2).toString(16).padStart(32,'0'),fingerprint:'f'.repeat(64)}));
 const f=await revisionHistory([{...sharedRow,capability_outcomes:[capability('started',jobBinding),capability('tool_failed',jobBinding)],shared_evidence:[{...sharedGroup,status:'tool_failed',linked_decisions:20,related_decisions:related,truncated:true}]}]);
 assert.match(flatten(f.elements.decisionHistoryList),/tool failed; this is not a code-defect finding/);assert.match(flatten(f.elements.decisionHistoryList),/additional references omitted/);assert.equal(f.submitted.length,0);
});
test('malformed shared provenance, contradictory outcomes and foreign fields fail closed before rendering',async()=>{
 for(const patch of [{reference:'<img>'},{kind:'model_acceptance'},{status:'checks_passed'},{linked_decisions:1},{linked_decisions:3},{linked_decisions:true},{related_decisions:[]},{related_decisions:[{id:sharedRow.id,fingerprint:sharedRow.fingerprint}]},{related_decisions:[{id:'f'.repeat(32),fingerprint:'bad'}]},{truncated:true},{read_only:false},{owner:'private'},{capability_revision_token:'a'.repeat(32)}]){
  const f=await revisionHistory([row,{...sharedRow,shared_evidence:[{...sharedGroup,...patch}]}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);
 }
 for(const patch of [{kind:'runtime_config'},{id:'short'},{fingerprint:'bad'},{decision:'request_changes'},{capability_outcomes:[]},{shared_evidence:[sharedGroup,sharedGroup]},{shared_evidence:null}]){const f=await revisionHistory([{...sharedRow,...patch}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);}
});

const counts={planned:1,executed:1,passed:1,failed:0,cancelled:0,timed_out:0};
const projectPass={...row,id:'a'.repeat(32),fingerprint:'b'.repeat(64),decision:'approve',feedback:'',outcomes:[outcome('started','started'),outcome('result','passed',{checks:counts,rollback:'not_attempted'})]};
const lifecycle=(extra={})=>({schema_version:1,state:'recorded',source:'project',freshness:'historical',reference:'9'.repeat(64),reason:'native_result_recorded',linked_decisions:1,related_decisions:[],truncated:false,read_only:true,...extra});
function displayOnly(node){assert(!['BUTTON','A','INPUT','TEXTAREA','SELECT'].includes(node.tagName));node.children.forEach(displayOnly);}
function noAuthority(f){assert.equal(f.submitted.length,0);assert.deepEqual(f.calls,[['/api/decisions/history']]);displayOnly(f.elements.decisionHistoryList);}
test('lifecycle states are explicitly historical and never turn observations into current validity',async()=>{
 const f=await revisionHistory([{...projectPass,evidence_lifecycle:lifecycle()}]);const text=flatten(f.elements.decisionHistoryList);
 assert.match(text,/Evidence lifecycle · Historical record.*A native result is recorded/);assert.match(text,/not a current validity check or execution authority/);assert.match(text,/Native checks passed/);assert.doesNotMatch(f.elements.decisionHistoryStatus.textContent,/unknown/);noAuthority(f);
});
test('manual restoration retains original pass while automatic rollback is a separate recorded fact',async()=>{
 const restored=[...projectPass.outcomes,outcome('undo_started','started',{event_id:'3'.repeat(32)}),outcome('undo_result','restored',{event_id:'4'.repeat(32),rollback:'restored'})];
 const f=await revisionHistory([{...projectPass,outcomes:restored,evidence_lifecycle:lifecycle({state:'restored',reason:'project_restored'})}]);const text=flatten(f.elements.decisionHistoryList);
 assert.match(text,/Manual restoration was recorded.*original native result is retained.*Native checks passed.*Manual restoration completed/);assert.match(text,/Each decision keeps its own observations and restoration history/);noAuthority(f);
 const g=await revisionHistory([{...projectPass,outcomes:[outcome('started','started'),outcome('result','failed',{checks:{...counts,passed:0,failed:1},rollback:'restored'})],evidence_lifecycle:lifecycle()}]);
 assert.match(flatten(g.elements.decisionHistoryList),/A native result is recorded.*Execution failed.*original content restored/);assert.doesNotMatch(flatten(g.elements.decisionHistoryList),/Manual restoration was recorded/);noAuthority(g);
});
test('conflicting worker observations retain both raw pass and fail, unrelated records and no resolution action',async()=>{
 const first={id:sharedRow.id,fingerprint:sharedRow.fingerprint},second={id:'f'.repeat(32),fingerprint:'f'.repeat(64)};
 const conflict=related=>lifecycle({state:'conflicting',source:'worker',reason:'native_identity_conflict',linked_decisions:2,related_decisions:[related]});
 const pass={...sharedRow,shared_evidence:[],capability_outcomes:[capability('started',jobBinding),capability('checks_passed',{...jobBinding,checks:counts})],evidence_lifecycle:conflict(second)};
 const fail={...sharedRow,...second,shared_evidence:[],evidence_lifecycle:conflict(first)};
 const f=await revisionHistory([pass,fail,row]);const text=flatten(f.elements.decisionHistoryList);
 assert.equal(f.elements.decisionHistoryList.children.length,3);assert.equal((text.match(/Conflicting native observations/g)||[]).length,2);assert.match(text,/No result is selected as authoritative/);assert.match(text,/Native worker checks passed/);assert.match(text,/Native worker checks found failures/);assert.match(text,/Changes requested/);assert.doesNotMatch(text,/Shared native worker observation/);noAuthority(f);
});
test('conflicting project identity does not erase a row-local Undo or the original failed result',async()=>{
 const other={id:'f'.repeat(32),fingerprint:'f'.repeat(64)},here={id:projectPass.id,fingerprint:projectPass.fingerprint};
 const conflict=related=>lifecycle({state:'conflicting',reason:'native_identity_conflict',linked_decisions:2,related_decisions:[related]});
 const f=await revisionHistory([{...projectPass,outcomes:[...projectPass.outcomes,outcome('undo_started','started',{event_id:'3'.repeat(32)}),outcome('undo_result','restored',{event_id:'4'.repeat(32),rollback:'restored'})],evidence_lifecycle:conflict(other)},{...projectPass,...other,outcomes:[outcome('started','started'),outcome('result','failed',{checks:{...counts,passed:0,failed:1}})],evidence_lifecycle:conflict(here)}]);
 assert.equal(f.elements.decisionHistoryList.children.length,2);const text=flatten(f.elements.decisionHistoryList);assert.equal((text.match(/Conflicting native observations/g)||[]).length,2);assert.match(text,/Native checks passed.*Manual restoration completed.*Execution failed/);noAuthority(f);
});
test('missing, pending and unconfirmed lifecycle states never claim a completed result',async()=>{
 const cases=[
  [{...projectPass,decision:'reject',outcomes:[]},lifecycle({state:'not_applicable',source:'none',reference:null,reason:'decision_not_approved',linked_decisions:0}),/authorized no execution/],
  [{...projectPass,outcomes:[]},lifecycle({state:'missing',reference:null,reason:'no_recorded_start',linked_decisions:0}),/No execution start is recorded; its effect is unknown/],
  [{...projectPass,outcomes:[outcome('started','started')]},lifecycle({state:'pending',reference:null,reason:'execution_pending',linked_decisions:0}),/Execution is pending.*no completed result is confirmed/],
  [{...projectPass,outcomes:[outcome('started','started'),outcome('result','unknown')]},lifecycle({state:'unconfirmed',reference:null,reason:'outcome_unconfirmed',linked_decisions:0}),/recorded execution outcome is unconfirmed/],
  [{...sharedRow,shared_evidence:[],capability_outcomes:[capability('started',jobBinding),capability('waiting',jobBinding)]},lifecycle({state:'pending',source:'worker',reason:'execution_pending'}),/Execution is pending.*Waiting at the approved destination/],
  [{...sharedRow,shared_evidence:[],capability_outcomes:[capability('started',jobBinding),capability('unconfirmed',jobBinding)]},lifecycle({state:'unconfirmed',source:'worker',reason:'outcome_unconfirmed'}),/recorded execution outcome is unconfirmed.*Execution unconfirmed/]
 ];
 for(const [base,projection,expected] of cases){const f=await revisionHistory([{...base,evidence_lifecycle:projection}]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,expected);assert.doesNotMatch(text,/A native result is recorded|lifecycle unknown/);noAuthority(f);}
});
test('pending and failed restoration summaries retain the original outcome',async()=>{
 const undo=outcome('undo_started','started',{event_id:'3'.repeat(32)});
 for(const [outcomes,projection,expected] of [
  [[...projectPass.outcomes,undo],lifecycle({state:'unconfirmed',reason:'restoration_pending'}),/Restoration was started; completion is not confirmed/],
  [[...projectPass.outcomes,undo,outcome('undo_result','restore_failed',{event_id:'4'.repeat(32),rollback:'needs_attention'})],lifecycle({state:'unconfirmed',reason:'restoration_unconfirmed'}),/Restoration is unconfirmed or needs attention/],
  [[outcome('started','started'),outcome('result','failed',{rollback:'needs_attention'})],lifecycle({state:'unconfirmed',reason:'restoration_unconfirmed'}),/Restoration is unconfirmed or needs attention/]
 ]){const f=await revisionHistory([{...projectPass,outcomes,evidence_lifecycle:projection}]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,expected);assert.match(text,/Native checks passed|Execution failed/);assert.doesNotMatch(text,/lifecycle unknown/);noAuthority(f);}
});
test('settings observations remain limited and legacy omission is supported without inventing a lifecycle',async()=>{
 const f=await capabilityHistory('runtime_config',[capability('started'),capability('settings_saved')],{id:projectPass.id,fingerprint:projectPass.fingerprint,evidence_lifecycle:lifecycle({source:'capability',reference:null,linked_decisions:0})});
 assert.match(flatten(f.elements.decisionHistoryList),/A native result is recorded.*future behavior has not been verified/);noAuthority(f);
 const legacy=await revisionHistory([projectPass]);assert.match(flatten(legacy.elements.decisionHistoryList),/Native checks passed/);assert.doesNotMatch(flatten(legacy.elements.decisionHistoryList),/Evidence lifecycle|current validity/);noAuthority(legacy);
});
test('malformed lifecycle summaries become unknown while valid raw observations remain visible',async()=>{
 const bad=[null,[],{...lifecycle(),schema_version:2},{...lifecycle(),state:'approved'},{...lifecycle(),source:'worker'},{...lifecycle(),freshness:'current'},{...lifecycle(),reason:['native_result_recorded']},{...lifecycle(),reason:'project_restored',state:'restored'},{...lifecycle(),read_only:false},{...lifecycle(),reference:'bad'},{...lifecycle(),reference:null},{...lifecycle(),linked_decisions:true},{...lifecycle(),linked_decisions:0},{...lifecycle(),linked_decisions:2},{...lifecycle(),truncated:true},{...lifecycle(),owner:'private'},{...lifecycle(),capability_revision_token:'secret'},Object.fromEntries(Object.entries(lifecycle()).filter(([key])=>key!=='reason'))];
 for(const value of bad){const f=await revisionHistory([{...projectPass,evidence_lifecycle:value},row]);const text=flatten(f.elements.decisionHistoryList);assert.equal(f.elements.decisionHistoryList.children.length,2);assert.match(text,/Evidence lifecycle unknown/);assert.match(text,/Native checks passed/);assert.match(f.elements.decisionHistoryStatus.textContent,/lifecycle summaries are unknown/);assert.doesNotMatch(text,/A native result is recorded|Manual restoration was recorded|secret|private/);noAuthority(f);}
});
test('lifecycle related references validate exact IDs, fingerprints, membership and bounded counts',async()=>{
 const related={id:'f'.repeat(32),fingerprint:'f'.repeat(64)},linked=lifecycle({linked_decisions:2,related_decisions:[related]});
 for(const patch of [{related_decisions:[{...related,id:'short'}]},{related_decisions:[{...related,fingerprint:'bad'}]},{related_decisions:[{id:projectPass.id,fingerprint:projectPass.fingerprint}]},{related_decisions:[{...related,owner:'secret'}]},{related_decisions:[related,related],linked_decisions:3},{related_decisions:Array(17).fill(related),linked_decisions:18,truncated:true},{linked_decisions:3},{linked_decisions:Number.MAX_SAFE_INTEGER+1}]){
  const f=await revisionHistory([{...projectPass,evidence_lifecycle:{...linked,...patch}}]);assert.match(flatten(f.elements.decisionHistoryList),/Evidence lifecycle unknown.*Native checks passed/);assert.doesNotMatch(flatten(f.elements.decisionHistoryList),/Related owner review references/);noAuthority(f);
 }
 const refs=Array.from({length:16},(_,i)=>({id:(i+2).toString(16).padStart(32,'0'),fingerprint:'f'.repeat(64)}));
 const f=await revisionHistory([{...projectPass,evidence_lifecycle:lifecycle({linked_decisions:20,related_decisions:refs,truncated:true})}]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,/20 recorded decisions linked.*additional references omitted/);assert(refs.every(ref=>text.includes(ref.id+' / '+ref.fingerprint)));noAuthority(f);
 const over=await revisionHistory([{...projectPass,evidence_lifecycle:lifecycle({linked_decisions:8193,related_decisions:refs,truncated:true})}]);assert.match(flatten(over.elements.decisionHistoryList),/Evidence lifecycle unknown/);assert.doesNotMatch(flatten(over.elements.decisionHistoryList),/8193/);
});
test('lifecycle metadata injection is neither rendered nor used as a navigation target',async()=>{
 const injected='<img src=x onerror=alert(1)>';
 for(const patch of [{reason:injected},{source:injected},{state:injected},{reference:injected},{related_decisions:[{id:injected,fingerprint:'f'.repeat(64)}],linked_decisions:2}]){
  const f=await revisionHistory([{...projectPass,evidence_lifecycle:{...lifecycle(),...patch}}]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,/Evidence lifecycle unknown/);assert(!text.includes(injected));noAuthority(f);
 }
});
test('conflict cannot be claimed without related native identity or mixed with consistent shared evidence',async()=>{
 for(const [base,projection] of [[projectPass,lifecycle({state:'conflicting',reason:'native_identity_conflict'})],[{...sharedRow},lifecycle({state:'conflicting',source:'worker',reason:'native_identity_conflict',linked_decisions:2,related_decisions:sharedGroup.related_decisions})],[{...projectPass,outcomes:[]},lifecycle({state:'conflicting',source:'none',reference:null,linked_decisions:0,reason:'native_identity_conflict'})]]){
  const f=await revisionHistory([{...base,evidence_lifecycle:projection}]);assert.match(flatten(f.elements.decisionHistoryList),/Evidence lifecycle unknown/);assert.doesNotMatch(flatten(f.elements.decisionHistoryList),/No result is selected as authoritative/);noAuthority(f);
 }
});
test('project provenance refers to the original result and retains its independent manual restoration',async()=>{
 const group={...sharedGroup,kind:'project_result',status:'passed'};
 const f=await revisionHistory([{...projectPass,shared_evidence:[group],outcomes:[...projectPass.outcomes,outcome('undo_started','started',{event_id:'3'.repeat(32)}),outcome('undo_result','restored',{event_id:'4'.repeat(32),rollback:'restored'})],evidence_lifecycle:lifecycle({state:'restored',reason:'project_restored',linked_decisions:2,related_decisions:group.related_decisions})}]);const text=flatten(f.elements.decisionHistoryList);
 assert.match(text,/Manual restoration completed.*Shared native project observation · Native checks passed/);assert.match(text,/Read-only provenance for this owner’s exact project result/);noAuthority(f);
 for(const patch of [{kind:'worker_result'},{status:'restored'},{status:'failed'}]){const g=await revisionHistory([{...projectPass,shared_evidence:[{...group,...patch}]}]);assert.match(g.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(g.elements.decisionHistoryList.children.length,0);}
 const noIdentity=await revisionHistory([{...projectPass,outcomes:[outcome('started','started'),outcome('result','unchanged',{edit_id:''})],shared_evidence:[{...group,status:'unchanged'}]}]);assert.match(noIdentity.elements.decisionHistoryStatus.textContent,/unavailable/);
});
test('late lifecycle conflicts do not reappear after authentication is lost',async()=>{
 const f=fixture();let resolve;f.set(()=>new Promise(r=>resolve=r));f.busy();f.open();await settle();f.emit('prometheus-auth-lost');
 resolve({schema_version:1,read_only:true,items:[{...projectPass,evidence_lifecycle:lifecycle({state:'conflicting',reason:'native_identity_conflict',linked_decisions:2,related_decisions:sharedGroup.related_decisions})}]});await settle();assert.equal(f.elements.decisionHistoryList.children.length,0);assert.match(f.elements.decisionHistoryStatus.textContent,/Unlock/);assert.equal(f.submitted.length,0);
});

const applicationEvent=(status,index=0,extra={})=>({event_id:(index+1).toString(16).padStart(32,'0'),phase:status==='started'?'started':'observed',observed_at:index+1,status,job_id:'d'.repeat(32),candidate_fingerprint:'e'.repeat(64),evidence_sha256:['started','unconfirmed'].includes(status)?null:'c'.repeat(64),...extra});
const applicationRow=(states,reason='native_result_recorded',state='recorded')=>({...row,id:'a'.repeat(32),fingerprint:'b'.repeat(64),kind:'self_improve',decision:'approve',feedback:'',application_outcomes:['started',...states].map((s,i)=>applicationEvent(s,i)),evidence_lifecycle:lifecycle({source:'application',reason,state})});
test('application activation history preserves native failures and rollback without manual Undo or current health claims',async()=>{
 const cases=[
  [applicationRow(['active']),/Application activation was recorded; this does not confirm the currently selected release/],
  [applicationRow(['failed']),/Application activation failed/],
  [applicationRow(['failed','rolled_back'],'application_restored','restored'),/Application activation failed.*Previous application release was restored/],
  [applicationRow(['recovery_required'],'restoration_unconfirmed','unconfirmed'),/Application recovery requires attention/],
  [applicationRow(['promotion_pending'],'execution_pending','pending'),/Exact application change queued; activation is not confirmed/],
  [applicationRow([],'outcome_unconfirmed','unconfirmed'),/dispatch is unconfirmed/],
  [applicationRow(['active','failed'],'native_identity_conflict','conflicting'),/No result is selected as authoritative.*Application activation was recorded.*Application activation failed/]
 ];
 for(const [item,expected] of cases){const f=await revisionHistory([item]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,expected);assert.doesNotMatch(text,/lifecycle unknown|Manual restoration|Native checks passed/);assert.match(text,/does not select a release, confirm current health, restore approval or repeat activation/);noAuthority(f);}
});
test('application lifecycle handles late pending and uncertain refresh without erasing historical active result',async()=>{
 for(const tail of ['promotion_pending','unconfirmed']){const f=await revisionHistory([applicationRow(['active',tail])]);const text=flatten(f.elements.decisionHistoryList);assert.match(text,/A native result is recorded/);assert.match(text,/Application activation was recorded/);assert.doesNotMatch(text,/lifecycle unknown/);noAuthority(f);}
});
test('malformed application observation identity, phase and native proof fail closed',async()=>{
 const initial=applicationEvent('started'),active=applicationEvent('active',1);
 const bad=[null,{},[active],[initial,{...active,job_id:'f'.repeat(32)}],[initial,{...active,candidate_fingerprint:'f'.repeat(64)}],[initial,{...active,status:'future'}],[initial,{...active,evidence_sha256:null}],[initial,{...active,error:'PRIVATE'}],[initial,{...active,phase:'started'}],[initial,{...active,event_id:initial.event_id}],[initial,active,applicationEvent('active',2)]];
 for(const events of bad){const f=await revisionHistory([{...applicationRow(['active']),application_outcomes:events}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);assert.equal(f.elements.decisionHistoryList.children.length,0);noAuthority(f);}
 for(const patch of [{kind:'runtime_config'},{decision:'reject'},{outcomes:[outcome('started','started')]}]){const f=await revisionHistory([{...applicationRow(['active']),...patch}]);assert.match(f.elements.decisionHistoryStatus.textContent,/unavailable/);noAuthority(f);}
});
test('false application lifecycle is unknown while original native observations remain',async()=>{
 const base=applicationRow(['failed']);
 for(const patch of [{reason:'application_restored',state:'restored'},{linked_decisions:2,related_decisions:[{id:'f'.repeat(32),fingerprint:'f'.repeat(64)}]},{reason:'native_identity_conflict',state:'conflicting'},{source:'worker'}]){const f=await revisionHistory([{...base,evidence_lifecycle:{...base.evidence_lifecycle,...patch}}]);assert.match(flatten(f.elements.decisionHistoryList),/Evidence lifecycle unknown.*Application activation failed/);noAuthority(f);}
 const f=await revisionHistory([{...applicationRow([]),application_outcomes:[],evidence_lifecycle:lifecycle({state:'missing',source:'application',reason:'no_recorded_start',reference:null,linked_decisions:0})}]);assert.match(flatten(f.elements.decisionHistoryList),/No execution start is recorded/);noAuthority(f);
});
