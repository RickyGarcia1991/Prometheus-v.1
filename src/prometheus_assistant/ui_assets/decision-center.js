(function () {
'use strict';
const el=id=>document.getElementById(id), box=el('decisionCenter'), status=el('decisionCenterStatus'), list=el('decisionCenterList'), refresh=el('decisionCenterRefresh');
if(!box || !status || !list || !refresh)return;
const interval=30000, sourceNames=['approvals','queue','activity','resources','devices'];
const destinations={project:'Code',scheduled:'Code',remote:'Remote',self_improvement:'Maintenance',research:'Maintenance',integrations:'Integrations'};
let state={online:false,busy:false,closed:false},generation=0,reading=false,refreshPending=false,timer=null,expiry=null,lastAttempt=-Infinity,generated=null;
const ready=()=>state.online && !state.busy && !state.closed && !document.hidden;
const text=value=>typeof value==='string'?value.slice(0,240):'';
function clear(message){const badge=el('reviewCount');if(badge){badge.hidden=true;badge.textContent='';}if(expiry!==null)clearTimeout(expiry);expiry=null;list.replaceChildren();list.hidden=true;generated=null;status.textContent=message;}
function stop(){if(timer!==null)clearTimeout(timer);timer=null;}
function controls(){refresh.disabled=!ready() || reading;}
function age(){return generated===null?null:Math.max(0,Math.floor(Date.now()/1000-generated));}
function schedule(){
 stop();controls();if(!ready() || reading)return;
 timer=setTimeout(()=>{timer=null;void load();},Math.max(0,interval-(Date.now()-lastAttempt)));
}
function validate(data){
 if(!data || data.schema_version!==1 || data.read_only!==true || !Number.isFinite(data.generated_at) || data.generated_at<0 || data.generated_at>Date.now()/1000+5 || !Array.isArray(data.items) || data.items.length>100)throw Error('Malformed snapshot');
 if(!data.sources || !sourceNames.every(name=>data.sources[name] && typeof data.sources[name].state==='string'))throw Error('Missing sources');
 const devices=data.sources.devices;
 if(devices.freshness==='current' && (!Number.isFinite(devices.observed_at) || !Number.isFinite(devices.expires_at) || devices.observed_at<0 || devices.observed_at>data.generated_at || devices.expires_at-devices.observed_at!==30))throw Error('Malformed device freshness');
 if(!data.counts || !['critical','warning','info'].every(name=>Number.isSafeInteger(data.counts[name]) && data.counts[name]>=0))throw Error('Malformed counts');
 for(const row of data.items){
  if(!row || !['id','kind','label','state','source'].every(name=>typeof row[name]==='string') || !['critical','warning','info'].includes(row.severity) || !Number.isSafeInteger(row.count) || row.count<0)throw Error('Malformed item');
 }
 return data;
}
function render(data){
 const badge=el('reviewCount'),count=data.counts.critical+data.counts.warning;
 if(badge){badge.hidden=!count;badge.textContent=' '+count;badge.setAttribute('aria-label',count+' items need attention');}
 generated=data.generated_at;
 const devices=data.sources.devices,expiresAt=Math.min(data.generated_at+60,devices.freshness==='current'?devices.expires_at:Infinity);
 if(Date.now()/1000>=expiresAt){clear('Decision snapshot or device observation is stale. Refresh for current information.');return;}
 const unavailable=[...sourceNames,...(data.sources.decisions?['decisions']:[])].filter(name=>!['available','ok'].includes(data.sources[name].state));
 status.textContent=`${data.counts.critical} critical · ${data.counts.warning} warning · ${data.counts.info} information · ${age()} seconds old.`+(unavailable.length?' Sources unavailable: '+unavailable.join(', ')+'.':'')+(!data.items.length?(unavailable.length?' No current items from available sources.':' No items need attention in this snapshot.'):'');
 if(expiry!==null)clearTimeout(expiry);
 expiry=setTimeout(()=>{expiry=null;clear('Decision snapshot or device observation is stale. Refresh for current information.');},Math.max(1,expiresAt*1000-Date.now()));
 list.replaceChildren();list.hidden=!data.items.length;
 for(const row of data.items){
  const li=document.createElement('li'),p=document.createElement('p');
  p.textContent=`${text(row.label)} · ${row.severity} · ${text(row.state)} · ${row.count} · ${text(row.source)}`+(typeof row.freshness==='string'?' · '+text(row.freshness):'');li.append(p);
  const navigation=typeof row.navigation==='string'?row.navigation:null;
  if(navigation==='history' || navigation==='activity' || Object.prototype.hasOwnProperty.call(destinations,navigation)){
   const button=document.createElement('button');button.type='button';button.textContent=navigation==='history'?'View decision history':navigation==='activity'?'View activity':'Open '+destinations[navigation];
   button.onclick=()=>{if(!ready() || age()===null || age()>60)return;if(navigation==='activity'||navigation==='history'){const target=el(navigation==='history'?'decisionHistory':'taskActivity');if(target){target.open=true;target.scrollIntoView({block:'nearest'});}}else window.PrometheusWorkspace?.show(destinations[navigation]);};li.append(button);
  }
  list.append(li);
 }
}
async function load(explicit=false){
 if(!ready()){schedule();return;}
 if(reading){if(explicit)refreshPending=true;return;}
 if(!explicit && Date.now()-lastAttempt<interval){schedule();return;}
 const current=generation;reading=true;lastAttempt=Date.now();controls();
 if(age()!==null && age()>60)clear('Earlier decision snapshot is stale. Refreshing…');
 try{const data=await api('/api/decision-center');if(current!==generation || !ready() || refreshPending)return;render(validate(data));}
 catch(error){if(current===generation)clear('Decision information unavailable. Refresh when the local connection is ready.');}
 finally{if(current===generation){reading=false;if(refreshPending){refreshPending=false;void load(true);}else schedule();}}
}
function invalidate(message){generation++;reading=false;refreshPending=false;stop();clear(message);controls();}
refresh.onclick=()=>load(true);
// A completed connection update only re-reads the passive dashboard endpoint.
// It cannot refresh the provider, resume waiting work or authorize an action.
window.addEventListener('prometheus-remote-state-updated',()=>{void load(true);});
window.addEventListener('prometheus-busy',event=>{
 state={...state,...event.detail};
 if(!state.online || state.closed){invalidate('Decision information unavailable while disconnected or closed.');return;}
 if(state.busy){invalidate('Decision information paused while current work is busy.');return;}
 void load();
});
window.addEventListener('prometheus-auth-lost',()=>{state.online=false;invalidate('Decision information unavailable. Owner unlock is required.');});
document.addEventListener('visibilitychange',()=>{if(document.hidden){invalidate('Decision information paused while this page is hidden.');}else void load();});
clear('Decision information is waiting for the local connection.');controls();
})();
