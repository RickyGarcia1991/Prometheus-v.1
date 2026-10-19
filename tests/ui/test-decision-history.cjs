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
