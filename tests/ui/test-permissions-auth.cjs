'use strict';
// Integrated lifecycle regression adapted from independent review reproduction. Synthetic data only.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {setup} = require('./test-workspace.cjs');
const assets = path.resolve(__dirname,'../../src/prometheus_assistant/ui_assets');
const response = (data,status=200) => ({ok:status>=200&&status<300,status,json:async()=>data});
const flush = async () => {for(let i=0;i<50;i++) await Promise.resolve();};
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const inventory={notice:'Synthetic snapshot',generated_at_utc:'2026-10-12T00:00:00Z',policy_revision:'synthetic',
  coverage:{core_tools_evaluated:1,evidence_tools_listed:0,browser_modes_listed:0,unmapped_surfaces:['Synthetic incomplete coverage']},
  rows:[{id:'identity.owner',capability:'Owner session',category:'authentication_integrity',outcome:'allow',reason:'Synthetic unlocked session',
    scope:'Snapshot only',basis:'runtime_state',policy_source:'owner_access.py',enforcement_point:'HTTP',
    grant:{status:'owner_session',expires_in_seconds:75},adjustable:false,requirements:'Synthetic'}],
  preview_tools:[{worker:'math',name:'calculate',inputSchema:{type:'object',required:['expression']}}]};
async function harness(){
  const dom=setup({runWorkspace:false}),{context:c,elements:e,documentRoot}=dom;
  const queued=new Map(),requests=[],navigations=[];
  c.document.hidden=false;c.document.querySelectorAll=s=>documentRoot.querySelectorAll(s);c.document.addEventListener=()=>{};
  c.window.dispatchEvent=event=>dom.emit(event.type,event.detail);
  c.CustomEvent=class{constructor(type,options={}){this.type=type;this.detail=options.detail;}};
  c.matchMedia=()=>({matches:false,addEventListener(){}});
  c.PrometheusAdaptive={AdaptiveDisplay:class{update(){return {level:0,name:'Still',fps:0,size:0,reason:'Synthetic'};}}};
  c.Image=class{};c.performance={now:()=>0};c.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
  c.requestAnimationFrame=()=>0;c.cancelAnimationFrame=()=>{};c.setTimeout=()=>0;
  c.AbortSignal={timeout:()=>({})};c.location={replace:url=>navigations.push(url)};
  c.fetch=async(url,options={})=>{
    requests.push({url,method:options.method||'GET'});
    const pending=queued.get(url);if(pending?.length)return await pending.shift();
    if(url==='/api/bootstrap')return response({token:'synthetic-token',languages:['Auto'],providers:[]});
    if(url==='/api/sessions')return response([]);
    if(url==='/api/status')return response({busy:false,job:null,state:'idle',closing:false,hardware:{ram_gib:8,cpu_threads:4}});
    if(url==='/api/permissions')return response(inventory);
    if(url==='/api/permissions/preview')return response({outcome:'allow',reason:'Synthetic',scope:'Default only'});
    if(url==='/api/owner/lock')return response({locked:true});
    if(url==='/api/close')return response({closing:true,busy:false});
    throw Error('Unexpected synthetic fetch '+url);
  };
  let focused=null;
  for(const el of Object.values(e))el.focus=()=>{if(focused!==el){focused=el;el.dispatch('focus');}};
  const runtime=vm.createContext(c);
  for(const name of ['transport.js','app.js','workspace.js','permissions.js'])
    vm.runInContext(fs.readFileSync(path.join(assets,name),'utf8'),runtime,{filename:name});
  await flush();
  assert.equal(vm.runInContext('online',runtime),true);
  // Use the real workspace keyboard handler and a focus event to load the panel.
  e.tabDocuments.dispatch('keydown',{key:'End'});await flush();
  assert.equal(e.panelPermissions.hidden,false);assert.equal(e.permissionRows.children.length,1);
  return {...dom,queued,requests,navigations,run:s=>vm.runInContext(s,runtime)};
}
(async()=>{
  const results=[];
  for(const transition of ['lock','disconnect','close']){
    const h=await harness(),e=h.elements,held=deferred();
    e.permissionArguments.value='{"expression":"1+2"}';
    h.queued.set('/api/permissions/preview',[held.promise]);
    const pending=e.previewPermission.onclick();await flush();
    if(transition==='lock'){e.ownerLock.click();await flush();}
    if(transition==='disconnect'){
      h.queued.set('/api/status',[Promise.reject(Error('Synthetic disconnect'))]);
      await h.run('status()');
    }
    if(transition==='close'){await e.close.onclick();await flush();}
    held.resolve(response({outcome:'allow',reason:'LATE-SYNTHETIC-REPLY',scope:'Default only'}));
    await pending;await flush();
    assert.equal(e.permissionRows.children.length,0);
    assert.equal(e.permissionPreview.textContent,'');assert.equal(e.permissionArguments.value,'{}');
    results.push({case:'late_preview_after_'+transition,result:'pass',rows:0,preview:''});
  }
  const h=await harness(),e=h.elements,held=deferred();
  e.permissionArguments.value='{"expression":"SYNTHETIC-PRIVATE-TEXT"}';
  h.queued.set('/api/permissions/preview',[held.promise]);
  const pending=e.previewPermission.onclick();await flush();
  h.queued.set('/api/sessions',[response({error:'Unlock Prometheus with your passkey.'},401)]);
  await h.run('refreshHistory()');await flush();
  assert.deepEqual(h.navigations,['/']);
  const immediatelyAfter401={rows:e.permissionRows.children.length,arguments:e.permissionArguments.value};
  held.resolve(response({outcome:'allow',reason:'LATE-SYNTHETIC-REPLY',scope:'Default only'}));
  await pending;await flush();
  const lateRendered=e.permissionPreview.textContent.includes('LATE-SYNTHETIC-REPLY');
  assert.equal(immediatelyAfter401.rows,0);
  assert.equal(immediatelyAfter401.arguments,'{}');
  assert.equal(lateRendered,false,'shared 401 must invalidate pending permissions independently of navigation');
  assert.equal(e.previewPermission.disabled,true);
  h.emit('prometheus-busy',{online:true,busy:false,closed:false});
  assert.equal(e.previewPermission.disabled,true,'stale success cannot restore an expired document');
  results.push({case:'late_preview_after_other_api_401',result:'pass',navigation_requested:true,
    immediatelyAfter401,late_reply_rendered:lateRendered,
    limitation:'Synthetic pending-navigation window only; real navigation completion destroys the document. HTTP authorization is not bypassed.'});
  for(const endpoint of ['/api/permissions','/api/permissions/preview']){
    const q=await harness(), el=q.elements;
    el.permissionArguments.value='{"expression":"SYNTHETIC"}';
    q.queued.set(endpoint,[response({error:'Synthetic authentication expiry'},401)]);
    await (endpoint.endsWith('/preview')?el.previewPermission.onclick():el.refreshPermissions.onclick());
    await flush();
    assert.equal(el.permissionRows.children.length,0);assert.equal(el.permissionArguments.value,'{}');
    assert.equal(el.permissionPreview.textContent,'');assert.equal(el.refreshPermissions.disabled,true);
    results.push({case:'own_endpoint_401_'+endpoint,result:'pass'});
  }
  {
    const q=await harness(),el=q.elements,late=deferred();
    q.queued.set('/api/permissions',[late.promise]);
    const pending=el.refreshPermissions.onclick();await flush();
    q.queued.set('/api/sessions',[response({error:'Synthetic expiry'},401)]);
    await q.run('refreshHistory()');await flush();
    late.resolve(response(inventory));await pending;await flush();
    assert.equal(el.permissionRows.children.length,0);assert.equal(el.permissionArguments.value,'{}');
    results.push({case:'late_inventory_after_shared_401',result:'pass'});
  }
  console.log(JSON.stringify({actual_scripts:['transport.js','app.js','workspace.js','permissions.js'],results},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
