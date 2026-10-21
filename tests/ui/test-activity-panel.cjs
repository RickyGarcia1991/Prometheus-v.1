'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {fixture,settle}=require('./test-app.cjs');
const row=(extra={})=>({id:'task-one',revision:1,label:'Running project checks',stage:'Running project checks',
 state:'running',background:false,seconds:3,steps:[{message:'Running project checks',seconds:0}],...extra});

const diskSample=(extra={})=>({status:'observed',source:'windows_pdh',scope:'whole_device',age_seconds:3,
 volumes:[{role:'data',status:'mapped'}],devices:[{roles:['data'],interval_seconds:12,
 latency_ms:2.5,busy_percent:20,queue_length:1,transfers_per_second:4}],...extra});

test('disk dashboard qualifies physical-device readings and clears them when disconnected',async()=>{
 const h=await fixture();h.statusData.resource_status={mode:'Balanced',disk_observation:diskSample()};
 await h.run('status()');const text=h.elements.diskStatus.textContent;
 assert.match(text,/App data disk: latency 2.50 ms.*busy 20.00%.*1 queued now.*4.00\/s over 12.0 s/);
 assert.match(text,/Whole-device activity, including other apps; sample 3 s old/);
 assert.match(text,/not a saturation diagnosis/);
 assert.doesNotMatch(text,/healthy|saturated|job cost|prediction/i);
 h.server.statusFailure=true;await h.run('status()');
 assert.match(h.elements.diskStatus.textContent,/not current/);assert.doesNotMatch(h.elements.diskStatus.textContent,/2.50/);
});

test('first disk sample displays only instantaneous queue and a warming interval',async()=>{
 const h=await fixture();h.statusData.resource_status={disk_observation:diskSample({status:'warming_up',devices:[{
 roles:['data'],interval_seconds:null,latency_ms:null,busy_percent:null,queue_length:0,transfers_per_second:null}]})};
 await h.run('status()');const text=h.elements.diskStatus.textContent;
 assert.match(text,/latency unknown.*busy unknown.*0 queued now.*transfers unknown.*warming up/);
});

for(const change of [{stale:true},{age_seconds:61},{age_seconds:-1},{age_seconds:NaN},
 {status:'unavailable'},{source:'guessed'},{scope:'job'}]){
 test('invalid or stale disk envelope hides old numbers '+JSON.stringify(change),async()=>{
  const h=await fixture();h.statusData.resource_status={disk_observation:diskSample(change)};
  await h.run('status()');assert.match(h.elements.diskStatus.textContent,/unavailable/i);
  assert.doesNotMatch(h.elements.diskStatus.textContent,/2.50|20.00|4.00/);
 });
}

test('partial disk metrics and unsupported volumes remain unknown without leaking identifiers',async()=>{
 const h=await fixture();h.statusData.resource_status={disk_observation:diskSample({status:'partial',
 volumes:[{role:'project',status:'unavailable',reason_code:'<img src=x>'}],devices:[{roles:['data'],device_id:'private-id',
 interval_seconds:10,latency_ms:null,busy_percent:101,queue_length:-1,transfers_per_second:Infinity}]})};
 await h.run('status()');const text=h.elements.diskStatus.textContent;
 assert.match(text,/latency unknown.*busy unknown.*queue unknown.*transfers unknown/);
 assert.match(text,/Last opened project disk: mapping unavailable/);
 assert.doesNotMatch(text,/private-id|<img/);assert.equal(h.elements.diskStatus.children.length,0);
});

test('one shared physical disk labels both roles once',async()=>{
 const h=await fixture();const sample=diskSample();sample.devices[0].roles=['data','project'];
 sample.volumes.push({role:'project',status:'mapped'});
 h.statusData.resource_status={disk_observation:sample};await h.run('status()');
 const text=h.elements.diskStatus.textContent;
 assert.match(text,/App data and last opened project disk: latency 2.50 ms/);
 assert.equal((text.match(/latency/g)||[]).length,1);
});

test('a project switch clears displayed readings until a fresh sample',async()=>{
 const h=await fixture();h.statusData.resource_status={disk_observation:diskSample()};await h.run('status()');
 h.statusData.resource_status.disk_observation={status:'unavailable',stale:true,reason_code:'selection_changed',devices:[]};
 await h.run('status()');const text=h.elements.diskStatus.textContent;
 assert.match(text,/Project selection changed.*fresh disk sample.*Earlier readings cleared/);
 assert.doesNotMatch(text,/2.50|20.00|4.00/);
});

test('a lost resource response clears the previous disk observation',async()=>{
 const h=await fixture();h.statusData.resource_status={disk_observation:diskSample()};await h.run('status()');
 h.statusData.resource_status=null;await h.run('status()');assert.doesNotMatch(h.elements.diskStatus.textContent,/2.50/);
});

test('resource readings preserve unknowns and become stale on disconnect',async()=>{
 const h=await fixture();h.statusData.resource_status={mode:'Balanced',observations:{available_ram_gib:3,cpu_percent:null,battery_percent:80,plugged_in:true}};
 await h.run('status()');assert.match(h.elements.resourceStatus.textContent,/RAM available 3.0 GiB/);
 assert.match(h.elements.resourceStatus.textContent,/CPU unknown/);
 h.server.statusFailure=true;await h.run('status()');assert.match(h.elements.resourceStatus.textContent,/not current/);
});

test('battery observation describes whole-computer change and elapsed time',async()=>{
 const h=await fixture();h.statusData.resource_status={mode:'Balanced',observations:{battery_percent:78,plugged_in:false,
  battery_observation:{status:'observed',scope:'whole_device',percentage_points:2,interval_seconds:120}}};
 await h.run('status()');const text=h.elements.resourceStatus.textContent;
 assert.match(text,/Whole-computer battery change: down 2\.0 percentage points over 2\.0 min/);
 assert.match(text,/includes all activity/);assert.doesNotMatch(text,/hours remaining|job cost|predicted runtime/i);
 h.statusData.resource_status.observations.battery_observation.percentage_points=0;
 await h.run('status()');assert.match(h.elements.resourceStatus.textContent,/no percentage change observed/);
 assert.doesNotMatch(h.elements.resourceStatus.textContent,/zero energy|no energy|0\.0 percentage points/);
 h.server.statusFailure=true;await h.run('status()');assert.match(h.elements.resourceStatus.textContent,/not current/);
 assert.doesNotMatch(h.elements.resourceStatus.textContent,/down 2\.0/);
});

test('battery unavailable reason remains literal text',async()=>{
 const h=await fixture();h.statusData.resource_status={mode:'Idle',observations:{
  battery_observation:{status:'unavailable',scope:'whole_device',percentage_points:null,interval_seconds:null,
   reason:'Waiting for samples. <img src=x onerror=alert(1)>'}}};
 await h.run('status()');assert.match(h.elements.resourceStatus.textContent,/Battery change unavailable: Waiting for samples\. <img/);
 assert.equal(h.elements.resourceStatus.children.length,0);
});

for(const change of [{percentage_points:-1},{percentage_points:101},{percentage_points:NaN},
 {interval_seconds:0},{interval_seconds:901},{scope:'single_job'},{status:'complete'}]){
 test('malformed battery observation is unavailable: '+JSON.stringify(change),async()=>{
  const h=await fixture();h.statusData.resource_status={mode:'Balanced',observations:{
   battery_observation:{status:'observed',scope:'whole_device',percentage_points:1,interval_seconds:60,...change}}};
  await h.run('status()');assert.match(h.elements.resourceStatus.textContent,/Battery change unavailable/);
  assert.doesNotMatch(h.elements.resourceStatus.textContent,/Whole-computer battery change: down/);
 });
}

test('background work is visible without a foreground conversation request',async()=>{
 const h=await fixture();h.statusData.activity=[row({background:true,label:'Checking saved sources'})];
 await h.run('status()');
 assert.match(h.elements.taskActivitySummary.textContent,/Working/);
 const entry=h.elements.taskActivityList.children[0];
 assert.match(entry.children[0].children[1].textContent,/Background/);
 assert.equal(h.requests.filter(request=>request.url==='/api/jobs').length,0);
});

test('reloaded status includes completed and failed work without inventing success',async()=>{
 const h=await fixture();h.statusData.activity=[row({state:'error',stage:'Project checks failed'})];
 await h.run('status()');assert.match(h.elements.taskActivitySummary.textContent,/Needs attention.*Project checks failed/);
 h.server.statusFailure=true;await h.run('status()');
 assert.match(h.elements.taskActivitySummary.textContent,/Disconnected.*last known activity/);
});

test('a late running update cannot overwrite newer completion',async()=>{
 const h=await fixture();h.statusData.activity=[row({revision:5,state:'done',stage:'Reviewed changes applied'})];
 await h.run('status()');h.statusData.activity=[row({revision:3})];await h.run('status()');
 assert.match(h.elements.taskActivitySummary.textContent,/Finished.*Reviewed changes applied/);
 h.run('taskActivity.reset()');assert.match(h.elements.taskActivitySummary.textContent,/no tasks this session/);
});

test('job polling displays stage changes and terminal results in the Activity panel',async()=>{
 const h=await fixture();await h.run("submitAction({mode:'coding',prompt:'Draft a function'})");await settle();
 const id=h.run('job');h.jobs.set(id,{state:'running',stage:'Preparing code',activity:row({id,stage:'Preparing code'})});
 await h.run(`pollJob('${id}')`);assert.match(h.elements.taskActivitySummary.textContent,/Preparing code/);
 h.jobs.set(id,{state:'done',seconds:5,result:{reply:'suggestion'},activity:row({id,revision:2,state:'done',
  label:'<img src=x onerror=alert(1)>',target:'example.py',stage:'Code suggestion ready · no files changed'})});
 await h.run(`pollJob('${id}')`);assert.match(h.elements.taskActivitySummary.textContent,/no files changed/);
 const title=h.elements.taskActivityList.children[0].children[0].children[0];
 assert.equal(title.textContent,'<img src=x onerror=alert(1)> · example.py');assert.equal(title.children.length,0);
});
