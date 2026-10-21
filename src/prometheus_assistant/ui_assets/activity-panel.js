'use strict';
// Only observable server events are shown. No generated reasoning or file contents.
(() => {
 const states={running:'Working',done:'Finished',review:'Review ready',error:'Needs attention',cancelled:'Stopped'};
 function batteryText(observation){
  const row=observation&&typeof observation==='object'?observation:{};
  const points=row.percentage_points,seconds=row.interval_seconds;
  if(row.status==='observed'&&row.scope==='whole_device'&&Number.isFinite(points)&&points>=0&&points<=100&&
     Number.isFinite(seconds)&&seconds>=60&&seconds<=900){
   const change=points===0?'no percentage change observed':`down ${points.toFixed(1)} percentage points`;
   return `Whole-computer battery change: ${change} over ${(seconds/60).toFixed(1)} min; includes all activity.`;
  }
  const reason=row.status==='unavailable'&&row.scope==='whole_device'&&typeof row.reason==='string'?
   row.reason.slice(0,300):'Waiting for a valid observation.';
  return 'Battery change unavailable: '+reason;
 }
 function diskText(observation){
  const row=observation&&typeof observation==='object'?observation:{};
  if(row.reason_code==='selection_changed')return 'Project selection changed; waiting for a fresh disk sample. Earlier readings cleared.';
  const validAge=Number.isFinite(row.age_seconds)&&row.age_seconds>=0&&row.age_seconds<=60;
  if(row.stale===true||!validAge)return 'Physical disk readings unavailable or stale; waiting for a fresh sample.';
  if(row.scope!=='whole_device'||row.source!=='windows_pdh'||
     !['observed','partial','warming_up'].includes(row.status)){
   return 'Physical disk readings unavailable; the device mapping or counters could not be verified.';
  }
  const devices=Array.isArray(row.devices)?row.devices.slice(0,2):[];
  const volumeRows=Array.isArray(row.volumes)?row.volumes.slice(0,2):[];
  const label=roles=>{
   const data=Array.isArray(roles)&&roles.includes('data'),project=Array.isArray(roles)&&roles.includes('project');
   return data&&project?'App data and last opened project disk':data?'App data disk':project?'Last opened project disk':'Disk';
  };
  const valid=(value,max=Number.MAX_SAFE_INTEGER)=>Number.isFinite(value)&&value>=0&&value<=max;
  const lines=devices.map(device=>{
   const interval=valid(device.interval_seconds,60)&&device.interval_seconds>=10;
   const metric=(key,suffix,max)=>interval&&valid(device[key],max)?device[key].toFixed(2)+suffix:'unknown';
   const queue=valid(device.queue_length)?device.queue_length.toFixed(0)+' queued now':'queue unknown';
   return `${label(device.roles)}: latency ${metric('latency_ms',' ms')} · busy ${metric('busy_percent','%',100)} · ${queue} · transfers ${metric('transfers_per_second','/s')}${interval?` over ${device.interval_seconds.toFixed(1)} s`:' · interval warming up'}.`;
  });
  for(const volume of volumeRows){if(volume.status!=='mapped')lines.push(`${label([volume.role])}: mapping unavailable.`);}
  if(!lines.length)return 'Physical disk readings unavailable; waiting for a verified device mapping.';
  return lines.join(' ')+` Whole-device activity, including other apps; sample ${row.age_seconds.toFixed(0)} s old. Instantaneous queue is not a saturation diagnosis. Refreshed at most every 10 seconds; unavailable readings retry after 60 seconds.`;
 }
 class ActivityPanel {
  constructor(document){this.document=document;this.rows=new Map();this.connected=true;this.signature='';}
  reset(){this.rows.clear();this.signature='';this.render();}
  update(rows){
   if(!Array.isArray(rows))return;
   this.connected=true;
   for(const row of rows){if(!row||!row.id)continue;const old=this.rows.get(row.id);if(!old||row.revision>=old.revision)this.rows.set(row.id,row);}
   const retained=[...this.rows.values()].sort((a,b)=>(b.revision||0)-(a.revision||0)).slice(0,12);
   this.rows=new Map(retained.map(row=>[row.id,row]));
   this.render();
  }
  resources(data){
   const preference=this.document.getElementById('resourceModeStatus'),select=this.document.getElementById('resourceMode'),save=this.document.getElementById('saveResourceMode');
   const p=data?.mode_preference;
   if(preference)preference.textContent=p?`Saved preference: ${p.requested||'Automatic'} · Effective: ${p.effective||'Unknown'}. ${p.reason||''}${typeof p.diagnostic==='string'?' '+p.diagnostic.slice(0,300):''}`:'Scheduling preference unavailable.';
   if(select){select.disabled=!p||select.dataset.saving==='true';if(p&&!select.dataset.edited&&select.dataset.saving!=='true')select.value=p.requested||'';}
   if(save)save.disabled=!p||select?.dataset.saving==='true';
   const disk=this.document.getElementById('diskStatus');if(disk)disk.textContent=diskText(data?.disk_observation);
   const box=this.document.getElementById('resourceStatus');if(!box)return;
   if(!data){box.textContent='Resource readings unavailable.';return;}
   const o=data.observations||{},number=(v,suffix)=>Number.isFinite(v)?v.toFixed(1)+suffix:'unknown';
   box.textContent=`Resources: ${data.mode||'Unknown'} · RAM available ${number(o.available_ram_gib,' GiB')} · CPU ${number(o.cpu_percent,'%')} · Battery ${number(o.battery_percent,'%')} · ${o.plugged_in===true?'AC power':o.plugged_in===false?'On battery':'Power unknown'}. ${batteryText(o.battery_observation)} ${data.error||'Readings cached for up to 10 seconds; project-task admission only.'}`;
  }
  offline(){this.connected=false;this.render();const box=this.document.getElementById('resourceStatus');if(box)box.textContent='Disconnected · resource readings are not current.';const disk=this.document.getElementById('diskStatus');if(disk)disk.textContent='Disconnected · physical disk readings are not current.';for(const id of ['resourceMode','saveResourceMode']){const element=this.document.getElementById(id);if(element)element.disabled=true;}const preference=this.document.getElementById('resourceModeStatus');if(preference)preference.textContent='Disconnected · scheduling preference is not current.';}
  render(){
   const summary=this.document.getElementById('taskActivitySummary'),list=this.document.getElementById('taskActivityList');
   if(!summary||!list)return;
   const rows=[...this.rows.values()].sort((a,b)=>(b.revision||0)-(a.revision||0)).slice(0,6),latest=rows[0];
   const message=!this.connected?'Disconnected · last known activity':!latest?'Ready · no tasks this session':
    `${states[latest.state]||'Task'} · ${latest.stage==='Completed · see the task result'?latest.label:latest.stage}`;
   if(summary.textContent!==message)summary.textContent=message;
   const signature=JSON.stringify([this.connected,rows.map(row=>[row.id,row.revision,Math.floor(row.seconds||0)])]);
   if(signature===this.signature)return;this.signature=signature;
   const nodes=rows.map(row=>{
    const item=this.document.createElement('li');item.className='task-activity-item';item.dataset.state=row.state;
    const title=this.document.createElement('div');title.className='task-activity-title';
    const label=this.document.createElement('strong');label.textContent=row.label+(row.target?' · '+row.target:'');
    const meta=this.document.createElement('span');meta.className='task-activity-meta';
    meta.textContent=`${row.background?'Background · ':''}${!this.connected&&row.state==='running'?'Last known: working':states[row.state]||'Task'} · ${Math.floor(row.seconds||0)}s`;
    title.append(label,meta);item.append(title);
    const steps=this.document.createElement('ol');steps.className='task-activity-steps';
    for(const step of (row.steps||[]).slice(-4)){const line=this.document.createElement('li');line.textContent=`${Math.floor(step.seconds||0)}s · ${step.message}`;steps.append(line);}
    item.append(steps);return item;
   });
   if(!nodes.length){const empty=this.document.createElement('li');empty.className='task-activity-empty';empty.textContent='Replies, file work and background checks will appear here.';nodes.push(empty);}
   list.replaceChildren(...nodes);
  }
 }
 window.PrometheusActivity={ActivityPanel};
})();
