'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test'),{setup}=require('./test-workspace.cjs');
const code=fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/interpretation-lifecycle.js'),'utf8');
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};
const hex=(c,n=64)=>c.repeat(n);
const replacement=()=>({target_id:hex('a',32),target_fingerprint:hex('b'),source:'worker',reference:hex('c'),snapshot_sha256:hex('d'),fact_sha256:hex('e'),selected_fact:{status:'checks_failed',checks:{planned:1,executed:1,passed:0,failed:1,cancelled:0,timed_out:0},rollback:null,evidence_sha256:hex('f')},reason:'Updated reason'});
const item=()=>({interpretation_id:hex('1',32),interpretation_fingerprint:hex('2'),target_id:hex('a',32),target_fingerprint:hex('b'),source:'worker',reference:hex('c'),state_sha256:hex('3'),state:'active',effective_payload:replacement(),effective_review_id:hex('1',32),effective_review_fingerprint:hex('2'),effective_change:null,currency:'matching_inputs',interpretation_conflict:false,history_conflict:false,total_changes:0,changes_truncated:false,changes:[],completion_unconfirmed:false});
const projection=x=>({schema_version:1,read_only:true,freshness:'historical',total:1,truncated:false,items:[x]});
const history=()=>({schema_version:1,read_only:true,items:[{id:hex('a',32),fingerprint:hex('b'),interpretation_lifecycle:projection(item()),evidence_conflict:{schema_version:1,read_only:true,state:'unresolved',freshness:'historical',requires_new_review:true,source:'worker',reference:hex('c'),snapshot_sha256:hex('d'),variants:[{fact_sha256:hex('e'),...replacement().selected_fact},{fact_sha256:hex('4'),status:'checks_passed',checks:{planned:1,executed:1,passed:1,failed:0,cancelled:0,timed_out:0},rollback:null,evidence_sha256:hex('5')}]}}]});
const pending=operation=>{const payload={interpretation_id:hex('1',32),interpretation_fingerprint:hex('2'),expected_state_sha256:hex('3'),operation:operation||'revise',reason:'Updated reason',replacement:operation==='withdraw'?null:replacement()};return {kind:'evidence_interpretation_change',id:hex('6',32),fingerprint:hex('7'),session:hex('8',32),policy_revision:'policy',payload,review:{summary:'Change a recorded owner interpretation.',effect:'Original interpretations, observations and failed checks remain in history. No execution or retry is authorized.',...JSON.parse(JSON.stringify(payload))}};};
function fixture(){
 const f=setup(),events={};let h=history(),p=null;f.context.session=hex('8',32);f.context.document.hidden=false;f.context.document.addEventListener=(n,fn)=>events[n]=fn;
 f.context.api=async url=>url==='/api/decisions/history'?(typeof h==='function'?h():h):{approval:p};
 vm.runInNewContext(code,f.context);f.busy=(busy=false)=>f.emit('prometheus-busy',{online:true,busy,closed:false});f.busy();
 f.open=async()=>{f.elements.interpretationLifecycle.open=true;f.elements.interpretationLifecycle.dispatch('toggle');await settle();};
 f.select=(operation='revise')=>{const e=f.elements;e.interpretationTarget.value=hex('1',32);e.interpretationTarget.dispatch('change');e.interpretationOperation.value=operation;e.interpretationOperation.dispatch('change');e.interpretationFact.value=hex('e');e.interpretationReason.value='Updated reason';e.interpretationReason.dispatch('input');};
 f.setHistory=v=>h=v;f.setPending=v=>p=v;f.hide=()=>{f.context.document.hidden=true;events.visibilitychange();};return f;
}
const text=e=>[e.textContent,...e.children.map(text)].join('\n');
test('revision binds current root state and native snapshot; decision uses frozen review',async()=>{
 const f=fixture(),e=f.elements;await f.open();f.select();assert(!e.interpretationPrepare.disabled);await e.interpretationPrepare.click();
 assert.deepEqual(f.submitted.at(-1),{mode:'interpretation_change_prepare',session:hex('8',32),interpretation_id:hex('1',32),interpretation_fingerprint:hex('2'),interpretation_state_sha256:hex('3'),interpretation_operation:'revise',interpretation_reason:'Updated reason',interpretation_snapshot_sha256:hex('d'),interpretation_fact_sha256:hex('e')});
 f.busy(true);f.result('interpretation_change_prepare',{approval:pending()},{origin:'local'});f.busy();assert(!e.interpretationApprove.disabled);e.interpretationReason.value='unreviewed';await e.interpretationApprove.click();
 assert.deepEqual(f.submitted.at(-1),{mode:'approval_decision',session:hex('8',32),approval_id:hex('6',32),approval_fingerprint:hex('7'),approval_decision:'approve'});
});
test('stale withdrawal needs no fresh fact and retains original text',async()=>{
 const f=fixture(),h=history();h.items[0].evidence_conflict=null;h.items[0].interpretation_lifecycle.items[0].currency='stale';f.setHistory(h);await f.open();f.select('withdraw');assert(!f.elements.interpretationPrepare.disabled);await f.elements.interpretationPrepare.click();assert(!Object.hasOwn(f.submitted.at(-1),'interpretation_fact_sha256'));assert(!Object.hasOwn(f.submitted.at(-1),'interpretation_snapshot_sha256'));assert.match(text(f.elements.interpretationDetails),/stale/i);
});
test('withdrawn or incomplete history cannot enable prepare',async()=>{
 for(const mutate of [x=>x.state='withdrawn',x=>x.state_sha256='bad',x=>delete x.effective_payload,x=>x.effective_payload.target_id=hex('9',32)]){
  const f=fixture(),h=history();mutate(h.items[0].interpretation_lifecycle.items[0]);f.setHistory(h);await f.open();f.select('withdraw');assert(f.elements.interpretationPrepare.disabled);assert.equal(f.submitted.length,0);
 }
});
for(const [name,mutate] of [['missing review',x=>delete x.review],['extra authority',x=>x.payload.command='run'],['wrong reason',x=>x.review.reason='different'],['wrong state',x=>x.review.expected_state_sha256=hex('9')],['missing replacement',x=>x.payload.replacement=null],['wrong counts',x=>x.review.replacement.selected_fact.checks.failed=0],['policy missing',x=>delete x.policy_revision],['wrong session',x=>x.session=hex('9',32)]])test(name+' cannot authorize a lifecycle change',async()=>{
 const f=fixture(),r=pending();mutate(r);f.setPending(r);await f.open();assert(f.elements.interpretationApprove.disabled);assert(f.elements.interpretationReject.disabled);assert(f.elements.interpretationReview.hidden);assert.equal(f.submitted.length,0);
});
test('pending withdrawal shows exact reason and can be denied',async()=>{
 const f=fixture();f.setPending(pending('withdraw'));await f.open();assert.match(text(f.elements.interpretationReview),/withdraw/);await f.elements.interpretationReject.click();assert.equal(f.submitted.at(-1).approval_decision,'reject');
});
test('late responses after auth loss, hidden tab, session or closure stay inert',async()=>{
 for(const boundary of ['auth','hidden','session','closed']){const f=fixture();let resolve;f.setHistory(()=>new Promise(r=>resolve=r));f.elements.interpretationLifecycle.open=true;f.elements.interpretationLifecycle.dispatch('toggle');await settle();
  if(boundary==='auth')f.emit('prometheus-auth-lost');if(boundary==='hidden')f.hide();if(boundary==='session'){f.context.session=hex('9',32);f.emit('prometheus-session-changed');}if(boundary==='closed'){f.elements.interpretationLifecycle.open=false;f.elements.interpretationLifecycle.dispatch('toggle');}
  resolve(history());await settle();assert.equal(f.elements.interpretationTarget.children.length,0);assert(f.elements.interpretationApprove.disabled);
 }
});
test('history text distinguishes past incomplete attempts from effective state and never submits',()=>{
 const f=fixture(),h=history(),x=h.items[0].interpretation_lifecycle.items[0];x.completion_unconfirmed=true;x.total_changes=1;x.changes=[{approval_id:hex('6',32),fingerprint:hex('7'),operation:'revise',reason:'<script>text only</script>',decision:'approve',recording_state:'unrecorded',created_at:1}];
 const box=f.context.document.createElement('li');f.context.window.PrometheusInterpretationLifecycle.renderHistory(box,h.items[0]);assert.match(text(box),/Earlier.*unconfirmed/i);assert.match(text(box),/Effective interpretation/);assert.match(text(box),/<script>/);assert.equal(f.submitted.length,0);
});
test('effective change remains provable beyond the truncated history window',()=>{
 const f=fixture(),h=history(),x=h.items[0].interpretation_lifecycle.items[0];
 x.effective_review_id=hex('6',32);x.effective_review_fingerprint=hex('7');x.effective_change={approval_id:hex('6',32),fingerprint:hex('7'),operation:'revise',reason:x.effective_payload.reason,decision:'approve',recording_state:'recorded',created_at:1};
 x.total_changes=18;x.changes_truncated=true;x.changes=Array.from({length:16},(_,i)=>({approval_id:i.toString(16).padStart(32,'0'),fingerprint:hex('9'),operation:'withdraw',reason:'Not accepted',decision:'reject',recording_state:'recorded',created_at:20-i}));
 const box=f.context.document.createElement('li');f.context.window.PrometheusInterpretationLifecycle.renderHistory(box,h.items[0]);assert.match(text(box),/Effective interpretation/);assert.doesNotMatch(text(box),/unavailable/);
});
test('unproven effective identities and contradictory duplicate roots are unavailable',async()=>{
 for(const mutate of [x=>x.effective_review_id=hex('9',32),x=>x.state='withdrawn',x=>x.effective_change={approval_id:hex('1',32),fingerprint:hex('2'),operation:'revise',reason:x.effective_payload.reason,decision:'reject',recording_state:'recorded',created_at:1}]){
  const f=fixture(),h=history();mutate(h.items[0].interpretation_lifecycle.items[0]);f.setHistory(h);await f.open();assert.match(f.elements.interpretationStatus.textContent,/unavailable/);assert(f.elements.interpretationPrepare.disabled);
 }
 const f=fixture(),h=history(),copy=JSON.parse(JSON.stringify(h.items[0]));copy.interpretation_lifecycle.items[0].history_conflict=true;h.items.push(copy);f.setHistory(h);await f.open();assert.match(f.elements.interpretationStatus.textContent,/unavailable/);
});
test('reason entry measures Unicode characters and prevents oversized preparation',async()=>{
 const f=fixture(),e=f.elements;await f.open();f.select('withdraw');assert.equal(e.interpretationReason.attrs.maxlength,'4000');e.interpretationReason.value='😀'.repeat(2000);e.interpretationReason.dispatch('input');assert(!e.interpretationPrepare.disabled);e.interpretationReason.value+='x';e.interpretationReason.dispatch('input');assert(e.interpretationPrepare.disabled);await e.interpretationPrepare.click();assert.equal(f.submitted.length,0);
});
