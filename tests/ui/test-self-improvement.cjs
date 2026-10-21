'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {setup}=require('./test-workspace.cjs');
const {elements:e,context,emit,submitted}=setup();
const pending={kind:'self_improve',id:'a'.repeat(32),fingerprint:'b'.repeat(64),session:'c'.repeat(32),
 review:{action:'Install exact tested edit',reason:'Correct empty input',effects:'Restart with rollback available',target:'src/example.py',
 diff:'-old\n+<script>untrusted literal diff</script>',check_summary:'42 tests passed',checks:[{id:'python',exit_code:0}]}};
let nextApproval=pending;
let activationResult={state:'promotion_pending'};
context.session='d'.repeat(32);context.api=async url=>{
 if(url==='/api/self-improvement/result')return activationResult;
 assert.equal(url,'/api/approvals/pending');return {approval:nextApproval};};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/self-improvement.js'),'utf8'),context);
function text(node){return node.textContent+node.children.map(text).join(' ');}
(async()=>{
 emit('prometheus-busy',{busy:false,online:true,closed:false});
 e.improvementGoal.value='Fix empty input';await e.prepareImprovement.click();
 assert.deepEqual(submitted.at(-1),{mode:'self_improve',prompt:'Fix empty input',session:'d'.repeat(32)});
 assert(e.improvementDecisions.hidden,'preparation does not imply approval');
 emit('prometheus-result',{origin:'observed',details:{approval:pending}});
 assert(e.improvementDecisions.hidden,'another tab cannot inject its pending review into this tab');
 emit('prometheus-result',{origin:'local',details:{approval:pending}});
 assert(!e.improvementDecisions.hidden);assert.match(text(e.improvementReview),/<script>untrusted literal diff<\/script>/);
 assert(!e.improvementReview.children.some(node=>node.tagName==='SCRIPT'));
 emit('prometheus-busy',{busy:true,online:true,closed:false});const before=submitted.length;
 await e.approveImprovement.click();assert.equal(submitted.length,before);
 emit('prometheus-busy',{busy:false,online:true,closed:false});
 await e.approveImprovement.click();assert.deepEqual(submitted.at(-1),{mode:'approval_decision',approval_decision:'approve',
  approval_id:pending.id,approval_fingerprint:pending.fingerprint,session:pending.session});
 await e.rejectImprovement.click();assert.equal(submitted.at(-1).approval_decision,'reject');
 emit('prometheus-result',{origin:'local',details:{approval_resolved:pending.id},reply:'Rejected. No change installed.'});
 assert(e.improvementDecisions.hidden);assert.match(e.improvementStatus.textContent,/Rejected/);
 await e.refreshApproval.click();assert(!e.improvementDecisions.hidden,'a refresh restores this owner’s exact pending review');
 nextApproval=null;await e.refreshApproval.click();assert(e.improvementDecisions.hidden);
 assert.match(e.improvementStatus.textContent,/No pending approval/);
 activationResult={state:'active',release:'Prometheus-reviewed'};await e.refreshActivation.click();
 assert.match(text(e.activationStatus),/activated successfully/);assert.match(e.activationStatus.className,/health-pass/);
 activationResult={state:'rolled_back',error:'Candidate could not start'};await e.refreshActivation.click();
 assert.match(text(e.activationStatus),/previous release was restored/);assert.match(e.activationStatus.className,/health-blocked/);
 activationResult={state:'recovery_required',recovery_error:'Owner recovery is needed'};await e.refreshActivation.click();
 assert.match(text(e.activationStatus),/Owner recovery is needed/);assert.match(e.activationStatus.className,/health-fail/);
 activationResult={state:'none'};await e.refreshActivation.click();assert(e.activationStatus.hidden);
 console.log('Self-improvement controls passed: explicit preparation, literal diff, exact approval binding, busy state, rejection and pending-review recovery.');
})().catch(error=>{console.error(error);process.exitCode=1;});
