'use strict';
(()=>{
 const element=id=>document.getElementById(id);
 let pending=null,activationLoaded=false;
 function text(tag,value,className=''){
  const node=document.createElement(tag);node.textContent=String(value||'');if(className)node.className=className;return node;
 }
 function render(approval){
  pending=approval;const box=element('improvementReview');box.replaceChildren();box.hidden=!approval;
  element('improvementDecisions').hidden=!approval;
  if(!approval)return;
  const review=approval.review;
  box.append(text('h3',review.action),text('p','Why: '+review.reason),text('p','Effects: '+review.effects),
   text('p',review.check_summary),text('p','Say or type “yes, approve” in this proposal’s conversation, or use the exact approval button below. Approval expires after ten minutes.','fine'));
  const diff=document.createElement('details');diff.className='workspace-detail';diff.open=true;
  diff.append(text('summary','Exact changes to '+review.target),text('pre',review.diff,'code-output'));
  box.append(diff);
  const checks=document.createElement('details');checks.className='workspace-detail';
  checks.append(text('summary','Required check results'),text('pre',JSON.stringify(review.checks,null,2),'code-output'));box.append(checks);
 }
 element('prepareImprovement').onclick=async()=>{
  const goal=element('improvementGoal').value.trim();if(!goal){element('improvementGoal').focus();return;}
  element('improvementStatus').textContent='Preparing a candidate and running its required tests. No installation has been approved.';
  await submitAction({mode:'self_improve',prompt:goal,...(session?{session}:{})});
 };
 element('refreshApproval').onclick=async()=>{
  try{const result=await api('/api/approvals/pending');render(result.approval);
   element('improvementStatus').textContent=result.approval?'Review the exact proposal below.':'No pending approval in this owner session.';
  }catch(error){element('improvementStatus').textContent=error.message;}
 };
 async function decide(decision){
  if(!pending)return;
  await submitAction({mode:'approval_decision',approval_decision:decision,approval_id:pending.id,
   approval_fingerprint:pending.fingerprint,session:pending.session});
 }
 element('approveImprovement').onclick=()=>decide('approve');
 element('rejectImprovement').onclick=()=>decide('reject');
 async function activation(){
  const box=element('activationStatus');
  try{
   const result=await api('/api/self-improvement/result');
   const labels={none:'No approved application change is recorded for this owner.',promotion_pending:'Activation queued. Waiting for final confirmation.',
    validating:'Validating the approved change.',retesting:'Rechecking the candidate before activation.',signing:'Signing the tested release.',
    waiting_for_shutdown:'Waiting for Prometheus to close before activation.',starting:'Starting the new release.',
    active:'The approved change was activated successfully.',rolled_back:'Activation did not complete. The previous release was restored.',
    failed:'Activation failed. Review the reported error.',recovery_required:'Activation needs recovery. Review the recovery details.',
    unconfirmed:'Activation could not be confirmed. Review the reported error before trying again.'};
   box.replaceChildren(text('strong',labels[result.state]||'Activation status is unavailable.'));
   box.className='workspace-card '+(result.state==='active'?'health-pass':['failed','recovery_required','unconfirmed'].includes(result.state)?'health-fail':result.state==='rolled_back'?'health-blocked':'health-untested');
   for(const [key,label] of [['release','Release'],['previous_release','Previous release'],['error','Error'],['recovery_error','Recovery']])
    if(result[key])box.append(text('p',label+': '+result[key],'fine'));
   box.hidden=result.state==='none';
  }catch(error){box.hidden=false;box.className='workspace-card health-blocked';box.replaceChildren(text('p','Activation status unavailable: '+error.message));}
 }
 element('refreshActivation').onclick=activation;
 window.addEventListener('prometheus-busy',event=>{if(event.detail.online&&!activationLoaded){activationLoaded=true;activation();}});
 window.addEventListener('prometheus-result',event=>{
  const result=event.detail;if(result.origin!=='local')return;
  if(result.details?.approval){render(result.details.approval);element('improvementStatus').textContent='Tests passed. Review the exact change before approving installation.';}
  if(result.details?.approval_resolved){render(null);element('improvementStatus').textContent=result.reply;if(result.details?.self_improvement)activation();}
  else if(['self_improve','approval_decision'].includes(result.action)&&result.error)element('improvementStatus').textContent=result.error;
  else if(result.action==='self_improve'&&result.details?.self_improvement&&!result.details?.approval)element('improvementStatus').textContent=result.reply;
 });
})();
