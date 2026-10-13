'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const {setup}=require('./test-workspace.cjs');
const {settle,deferred}=require('./test-app.cjs');
const assets=path.join(__dirname,'../../src/prometheus_assistant/ui_assets');
function text(node){return node.textContent+' '+node.children.map(text).join(' ');}
const approval=(kind='integration')=>({id:'a'.repeat(32),fingerprint:'b'.repeat(64),session:'c'.repeat(32),kind,
 review:{action:'Remember a note',reason:'Retain the requested preference',effects:'Write locally',duration:'This action once',resources:{project:'home',content:'<script>literal note</script>'}}});
function grant(overrides={}){return {id:'d'.repeat(48),state:'active',created_at:1,expires_at:null,contract:{task_id:'e'.repeat(32),task:'Maintain home preferences',adapter:'agentmemory',actions:['remember','recall'],resources:{project:'home'},duration_seconds:null},...overrides};}
function fixture(){
 const h=setup(),requests=[];h.context.session='f'.repeat(32);h.context.crypto=webcrypto;h.context.rememberSession=value=>{h.context.session=value;};
 const state={pending:null,providers:[{provider:'agentmemory',available:true,version:'0.9.30',integrity:'verified',operations:['remember','recall','forget'],limitations:['Local keyword matching.']},{provider:'browser_use',available:true,version:'fixture',integrity:'verified',operations:['read'],limitations:['No sign-in profile.']}],grants:[],held:null,failed:null};
 h.context.api=async(url,body)=>{requests.push({url,body:body?JSON.parse(JSON.stringify(body)):null});if(state.held){const held=state.held;state.held=null;return await held.promise;}if(state.failed){const error=state.failed;state.failed=null;throw error;}
  if(url==='/api/integrations')return {providers:state.providers,grants:state.grants};if(url==='/api/approvals/pending')return {approval:state.pending};
  if(url==='/api/integrations/revoke'){state.grants=state.grants.map(row=>row.id===body.id?{...row,state:'revoked'}:row);return {state:'revoked'};}
  if(url==='/api/integrations/select'){const selected=state.grants.find(row=>row.id===body.id);return {id:selected.id,contract:selected.contract,session:body.session||'1'.repeat(32)};}
  throw Error('Unexpected test request '+url);
 };
 vm.runInNewContext(fs.readFileSync(path.join(assets,'integrations.js'),'utf8'),h.context);
 return {...h,requests,state};
}
async function ready(h){h.emit('prometheus-busy',{online:true,busy:false,closed:false});await h.elements.tabIntegrations.click();await settle();}
function result(h,detail){h.emit('prometheus-result',{origin:'local',...detail});}
(async()=>{
 const h=fixture(),e=h.elements;assert(e.reviewIntegration.disabled);await ready(h);
 assert.equal(e.panelIntegrations.hidden,false);assert.equal(e.tabIntegrations.getAttribute('aria-selected'),'true');assert.equal(e.panelDocuments.hidden,true);
 e.tabMaintenance.dispatch('keydown',{key:'ArrowRight'});assert.equal(e.panelIntegrations.hidden,false,'new panel is keyboard reachable');e.tabIntegrations.dispatch('keydown',{key:'ArrowRight'});assert.equal(e.panelRemote.hidden,false);
 assert.match(text(e.integrationProviders),/AgentMemory.*Available/);assert.match(text(e.integrationProviders),/Local keyword matching/);assert.equal(e.integrationDuration.value,'until-revoked');
 assert(!e.reviewIntegration.disabled);assert.equal(h.submitted.length,0,'opening discovery cannot run an integration');
 await e.reviewIntegration.click();assert.equal(h.submitted.length,0);assert.match(e.integrationStatus.textContent,/detail to remember/);
 e.integrationProject.value='home';e.integrationContent.value='Use warm lighting';await e.reviewIntegration.click();
 assert.deepEqual(h.submitted.at(-1),{mode:'integration',adapter:'agentmemory',operation:'remember',arguments:{project:'home',content:'Use warm lighting'},session:'f'.repeat(32)});
 assert(!h.submitted.at(-1).task_grant,'default asks for one exact review');
 const pending=approval();result(h,{action:'integration',details:{approval:pending}});
 assert(!e.integrationReview.hidden);assert(!e.integrationDecisions.hidden);assert.match(text(e.integrationReview),/<script>literal note<\/script>/);assert(!e.integrationReview.children.some(row=>row.tagName==='SCRIPT'));
 assert(e.integrationContent.disabled&&e.reviewIntegration.disabled,'one pending exact proposal prevents silently replacing its fields');
 await e.approveIntegration.click();assert.deepEqual(h.submitted.at(-1),{mode:'approval_decision',approval_decision:'approve',approval_id:pending.id,approval_fingerprint:pending.fingerprint,session:pending.session});
 result(h,{action:'approval_decision',reply:'Saved.',details:{approval_resolved:pending.id,integration:{adapter:'agentmemory',operation:'remember',result:{id:'mem_one',content:'<img src=x>'}}}});
 assert(e.integrationReview.hidden);assert.match(e.integrationResult.textContent,/<img src=x>/);assert.equal(e.integrationResult.children.length,0,'provider output stays literal');
 e.integrationOperation.value='recall';e.integrationOperation.onchange();assert.equal(e.integrationQueryField.hidden,false);assert(e.integrationContentField.hidden);e.integrationQuery.value='lighting';await e.reviewIntegration.click();
 assert.deepEqual(h.submitted.at(-1).arguments,{project:'home',query:'lighting'});
 e.integrationOperation.value='forget';e.integrationOperation.onchange();e.integrationMemoryIds.value='mem_one, mem_two';e.integrationForgetReason.value='Outdated';await e.reviewIntegration.click();
 assert.deepEqual(h.submitted.at(-1).arguments,{project:'home',memory_ids:['mem_one','mem_two'],reason:'Outdated'});
 e.integrationTask.value='Maintain home preferences';e.integrationAllowRemember.checked=true;e.integrationAllowRecall.checked=true;await e.reviewIntegrationAccess.click();
 const task=h.submitted.at(-1);assert.equal(task.mode,'integration_access');assert.equal(task.task.duration_seconds,null);assert.deepEqual(task.task.actions,['remember','recall']);assert.deepEqual(task.task.resources,{project:'home'});assert.match(task.task.task_id,/^[a-f0-9]{32}$/);
 assert(!task.task.actions.includes('forget'),'destructive memory access is never selected implicitly');
 e.integrationDuration.value='3600';e.integrationDuration.onchange();await e.reviewIntegrationAccess.click();assert.equal(h.submitted.at(-1).task.duration_seconds,3600);assert.notEqual(h.submitted.at(-1).task.task_id,task.task.task_id,'each task review has a fresh random identity');
 const access=approval('integration_access');result(h,{details:{approval:access}});const issued=grant();result(h,{action:'approval_decision',reply:'Access active',details:{approval_resolved:access.id,task_grant:issued}});
 assert.match(text(e.integrationGrants),/Until you revoke it/);assert.equal(e.integrationGrant.options.length,1,'forget cannot reuse a remember/recall grant');
 e.integrationOperation.value='remember';e.integrationOperation.onchange();e.integrationGrant.value=issued.id;e.integrationGrant.onchange();assert.equal(e.reviewIntegration.textContent,'Run with approved task access');
 await e.reviewIntegration.click();assert.equal(h.submitted.at(-1).task_grant,issued.id);
 e.integrationProject.value='workshop';e.integrationProject.oninput();assert.equal(e.integrationGrant.value,'','changing project clears a mismatched grant');e.integrationProject.value='home';e.integrationProject.oninput();
 h.state.grants=[issued];await e.refreshIntegrations.click();h.emit('prometheus-busy',{online:true,busy:true,closed:false});const revoke=e.integrationGrants.children[0].children.find(row=>row.tagName==='BUTTON');assert(!revoke.disabled,'revocation remains available while an action is busy');await revoke.click();await settle();
 assert.deepEqual(h.requests.findLast(row=>row.url==='/api/integrations/revoke').body,{id:issued.id});assert.match(text(e.integrationGrants),/revoked/);assert.equal(e.integrationGrant.value,'');
 h.emit('prometheus-busy',{online:true,busy:false,closed:false});
 e.integrationAdapter.value='browser_use';e.integrationAdapter.onchange();assert(e.integrationProjectField.hidden);assert.equal(e.integrationUrlField.hidden,false);assert.equal(e.integrationOperation.value,'read');assert.equal(e.integrationScope.value,'selected-hosts');
 e.integrationUrl.value='https://d';e.integrationUrl.oninput();e.integrationUrl.value='https://docs.example.com/page';e.integrationUrl.oninput();assert.equal(e.integrationHosts.value,'docs.example.com','suggested host follows the completed URL while typing');await e.reviewIntegration.click();
 assert.deepEqual(h.submitted.at(-1).arguments,{url:'https://docs.example.com/page'});assert.equal(h.submitted.at(-1).adapter,'browser_use');
 e.integrationTask.value='Read the public documentation';await e.reviewIntegrationAccess.click();assert.deepEqual(h.submitted.at(-1).task.resources,{hosts:['docs.example.com']});assert.deepEqual(h.submitted.at(-1).task.actions,['read']);
 e.integrationScope.value='public-web';e.integrationScope.onchange();assert(e.integrationHostsField.hidden);await e.reviewIntegrationAccess.click();assert.deepEqual(h.submitted.at(-1).task.resources,{public_web:true},'broader web access requires an explicit scope choice');
 const beforeInvalid=h.submitted.length;e.integrationUrl.value='https://user:password@example.com';await e.reviewIntegration.click();assert.equal(h.submitted.length,beforeInvalid);assert.match(e.integrationStatus.textContent,/without account credentials/);

 const isolate=fixture();await ready(isolate);isolate.state.pending=approval('self_improve');await isolate.elements.refreshIntegrationApproval.click();assert(isolate.elements.integrationReview.hidden);assert.match(isolate.elements.integrationStatus.textContent,/Maintenance/);
 isolate.emit('prometheus-result',{origin:'observed',details:{approval:approval()}});assert(isolate.elements.integrationReview.hidden,'another tab cannot inject an approval');
 isolate.state.pending=approval();await isolate.elements.refreshIntegrationApproval.click();assert(!isolate.elements.integrationReview.hidden);
 await isolate.elements.rejectIntegration.click();assert.equal(isolate.submitted.at(-1).approval_decision,'reject');result(isolate,{action:'chat',reply:'Rejected',details:{approval_resolved:approval().id}});assert(isolate.elements.integrationReview.hidden,'typed or voice resolution clears the same exact review');
 isolate.state.pending=null;isolate.state.failed=Error('Temporary failure');const beforeFailure=isolate.requests.length;await isolate.elements.refreshIntegrations.click();assert.equal(isolate.requests.length,beforeFailure+1,'failed reads are not replayed');assert.match(isolate.elements.integrationStatus.textContent,/Temporary failure/);
 const unavailable=fixture();unavailable.state.providers[0].available=false;unavailable.state.providers[0].integrity='failed';await ready(unavailable);assert(unavailable.elements.reviewIntegration.disabled);assert.match(text(unavailable.elements.integrationProviders),/Unavailable/);

 const resume=fixture();resume.state.grants=[grant()];await ready(resume);const useButton=()=>resume.elements.integrationGrants.children[0].children.find(row=>row.textContent==='Use in this conversation');
 await useButton().click();assert.deepEqual(resume.requests.findLast(row=>row.url==='/api/integrations/select').body,{id:grant().id,session:'f'.repeat(32)});assert.equal(resume.context.session,'f'.repeat(32));
 assert.equal(resume.submitted.length,0,'resuming a previously approved task neither reapproves nor executes an action');assert.equal(resume.elements.integrationStatus.textContent,'Using task: Maintain home preferences');
 resume.context.session=null;resume.emit('prometheus-show-conversation',{});assert.match(resume.elements.integrationStatus.textContent,/no task selected/);
 await useButton().click();assert.deepEqual(resume.requests.findLast(row=>row.url==='/api/integrations/select').body,{id:grant().id});assert.equal(resume.context.session,'1'.repeat(32),'selection remembers the server-created empty conversation');
 resume.emit('prometheus-busy',{online:true,busy:true,closed:false});assert(useButton().disabled,'an active job cannot silently switch its conversation purpose');
 resume.emit('prometheus-busy',{online:true,busy:false,closed:false});const heldSelection=deferred();resume.state.held=heldSelection;const selecting=useButton().click();await settle();
 resume.context.session='2'.repeat(32);resume.emit('prometheus-show-conversation',{});heldSelection.resolve({id:grant().id,contract:grant().contract,session:'1'.repeat(32)});await selecting;assert.equal(resume.context.session,'2'.repeat(32),'late task selection cannot restore a previously displayed conversation');
 await useButton().click();await resume.elements.integrationGrants.children[0].children.find(row=>row.textContent==='Revoke task access').click();await settle();assert.match(resume.elements.integrationStatus.textContent,/revoked/);assert(useButton().disabled);assert(!resume.elements.integrationStatus.textContent.startsWith('Using task:'));

 for(const interruption of ['lock','auth','disconnect','close']){
  const stale=fixture();await ready(stale);const held=deferred();stale.state.held=held;const loading=stale.elements.refreshIntegrations.click();await settle();
  stale.elements.integrationContent.value='Private note';if(interruption==='lock')stale.elements.ownerLock.click();if(interruption==='auth')stale.emit('prometheus-auth-lost',{});
  if(interruption==='disconnect')stale.emit('prometheus-busy',{online:false,busy:false,closed:false});if(interruption==='close')stale.emit('prometheus-busy',{online:true,busy:false,closed:true});
  held.resolve({providers:stale.state.providers,grants:[grant()]});await loading;await settle();
  assert.equal(stale.elements.integrationProviders.children.length,0,interruption+' discards late inventory');assert.equal(stale.elements.integrationGrants.children.length,0);assert.equal(stale.elements.integrationContent.value,'');assert(stale.elements.reviewIntegration.disabled);
 }
 // Separate approval renderers must not claim unrelated integrations passed code tests.
 const separate=setup();separate.context.api=async()=>({approval:approval(),state:'none'});separate.context.session='f'.repeat(32);
 vm.runInNewContext(fs.readFileSync(path.join(assets,'self-improvement.js'),'utf8'),separate.context);separate.emit('prometheus-busy',{online:true,busy:false,closed:false});
 separate.emit('prometheus-result',{origin:'local',details:{approval:approval()}});assert(separate.elements.improvementReview.hidden);assert(!separate.elements.improvementStatus.textContent.includes('Tests passed'));
 await separate.elements.refreshApproval.click();assert.match(separate.elements.improvementStatus.textContent,/Integrations/);
 console.log('Integrations UI PASS: discovery, exact action and task contracts, explicit duration/resources, literal reviews/results, exact approvals, grants and busy revocation, broad-web opt-in, validation, kind isolation and stale auth/disconnect cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
