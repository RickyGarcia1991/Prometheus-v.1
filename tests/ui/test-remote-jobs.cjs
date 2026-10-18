'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {setup}=require('./test-workspace.cjs');
const f=setup(),e=f.elements;
let expiration=null;
f.context.setTimeout=callback=>{expiration=callback;return 1;};
f.context.clearTimeout=()=>{expiration=null;};
f.context.session='new-session';
let pending=null,context={project:'C:\\Project',dirty:false,stale:false};
f.context.window.PrometheusWorkspace.context=()=>context;
f.context.api=async()=>({approval:pending});
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/remote-jobs.js'),'utf8'),f.context);
const row={id:'exact-id',session:'original-session',fingerprint:'exact-hash',kind:'remote_syntax',
  review:{action:'Check syntax',benefit:'Evidence',destination:{device_name:'Surface',device_id:'surface',account_id:'account',worker_digest:'hash'},
    files:[{path:'a.py',utf8_bytes:20,sha256:'filehash',content:'<script>untrusted</script>'}]}};
(async()=>{
 f.emit('prometheus-busy',{busy:false,online:true,closed:false});
 e.workerFiles.value='a.py';await e.workerPrepare.click();
 assert.equal(f.submitted.at(-1).project,'C:\\Project');assert.equal(f.submitted.at(-1).mode,'remote_syntax_prepare');
 f.result('remote_syntax_prepare',{approval:row});
 assert(!e.workerReview.hidden);assert.equal(e.workerReview.children.at(-1).textContent,'<script>untrusted</script>');
 e.workerFiles.value='changed.py';await e.workerApprove.click();
 assert.equal(f.submitted.at(-1).approval_fingerprint,'exact-hash');assert.equal(f.submitted.at(-1).session,'original-session');
 assert.equal(f.submitted.at(-1).remote_files,undefined);
 f.result('remote_syntax_prepare',{approval_resolved:'exact-id',remote_syntax:{job_id:'job',state:'unconfirmed'}});
 assert(e.workerReview.hidden);assert.match(e.workerJobStatus.textContent,/will not run again/);
 await e.workerRecover.click();assert.equal(f.submitted.at(-1).remote_job_id,'job');
 f.result('remote_syntax_prepare',{approval:row},{origin:'observed'});assert(e.workerReview.hidden);
 pending=row;await e.workerRefresh.click();assert(!e.workerReview.hidden);
 await e.workerDeny.click();assert.equal(f.submitted.at(-1).approval_decision,'reject');
 f.result('remote_syntax_prepare',{approval_resolved:'exact-id'});
 const sample={authenticated:true,decision:'ready',reasons:[],expires_at:Math.floor(Date.now()/1000)+20,
   observations:{available_ram_gib:4,cpu_percent:10,free_disk_gib:30,battery_percent:95,plugged_in:true}};
 f.result('remote_syntax_info',{remote_worker:{ready:true,target:{device_name:'Surface'},resource_admission:sample}});
 assert.match(e.workerResources.textContent,/4.0 GiB/);assert.match(e.workerResources.textContent,/Signed reading/);
 expiration();assert.match(e.workerResources.textContent,/expired/);
 f.result('remote_syntax_prepare',{remote_syntax:{job_id:'deferred-job',state:'deferred',reasons:['Battery power']}});
 assert.match(e.workerJobStatus.textContent,/No source transferred or job claimed/);
 assert.match(e.workerJobStatus.textContent,/Prepare a new review/);
 assert.doesNotMatch(e.workerJobStatus.textContent,/Use recovery/);
 const count=f.submitted.length;context.dirty=true;await e.workerPrepare.click();assert.equal(f.submitted.length,count);
 f.emit('prometheus-auth-lost');assert(e.workerApprove.disabled && e.workerReview.hidden && e.workerResources.hidden);
 console.log('Remote worker UI: exact source review, decisions, recovery, dirty editor and owner lock passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
