'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assets = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
class Element {
  constructor() { this.value = ''; this.children = []; this.textContent = ''; this.listeners = {}; this.disabled = false; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const ids = [...fs.readFileSync(path.join(assets,'index.html'),'utf8').matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
const elements = Object.fromEntries(ids.map(id=>[id,new Element()]));
elements.grantCapability.value='project/tests';elements.grantArguments.value='{"target":"tests"}';
elements.grantSeconds.value='300';elements.grantUses.value='1';
const listeners={},requests=[];
const grant={id:'a'.repeat(48),capability:'project/tests',target:'C:/fixture/tests',state:'active',remaining:1,expires:2000000000,scope:'scope-digest'};
let reply=null,jobState='done',listing=[grant];
async function api(route,body) {
  requests.push({route,body});
  if(reply){const value=reply;reply=null;return value;}
  if(route.endsWith('/review'))return {review_id:'review',scope_sha256:'scope-digest',target:grant.target,seconds:300,uses:1,policy_revision:'revision',notice:'Exact scope only.'};
  if(route.endsWith('/issue'))return grant;
  if(route.endsWith('/revoke'))return {state:'revoked'};
  if(route==='/api/jobs')return {id:'fast-job',state:'running'};
  if(route==='/api/job/fast-job')return {state:jobState,result:jobState==='done'?{reply:'Exact fixture result'}:undefined,error:jobState==='error'?'Grant expired before dispatch':undefined};
  return {grants:listing};
}
vm.runInNewContext(fs.readFileSync(path.join(assets,'grants.js'),'utf8'),{
  document:{getElementById:id=>{assert.ok(elements[id],id);return elements[id];},createElement:()=>new Element()},
  window:{addEventListener:(name,fn)=>{listeners[name]=fn;}},api,crypto:{randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef'},Date,setTimeout,console
});
(async()=>{
  assert.ok(elements.grantReview.disabled);assert.equal(requests.length,0);
  listeners['prometheus-busy']({detail:{online:true,busy:false}});
  await elements.grantReview.onclick();
  assert.equal(requests.length,1);assert.ok(!elements.grantIssue.disabled);
  assert.match(elements.grantReviewText.textContent,/"target": "tests"/);
  assert.match(elements.grantReviewText.textContent,/C:\/fixture\/tests/);
  assert.ok(requests.every(x=>!x.route.endsWith('/issue')),'review never creates a grant');
  elements.grantArguments.value='{"target":"different"}';elements.grantArguments.oninput();
  assert.ok(elements.grantIssue.disabled,'changing arguments invalidates the review');
  elements.grantArguments.value='{"target":"tests"}';await elements.grantReview.onclick();await elements.grantIssue.onclick();
  assert.equal(elements.grantRunId.value,grant.id);
  assert.match(elements.grantStatus.textContent,/no action has run/);
  assert.equal(elements.grantRows.children.length,1);
  await elements.grantRun.onclick();
  assert.match(elements.grantStatus.textContent,/completed/,'fast jobs complete without waiting for app status polling');
  assert.equal(elements.grantReviewText.textContent,'Exact fixture result');
  const body=requests.find(x=>x.route==='/api/jobs').body;
  assert.equal(body.mode,'core');assert.ok(!('prompt' in body));assert.ok(!('owner' in body));assert.equal(body.invocation_id.length,32);
  jobState='error';await elements.grantRun.onclick();
  assert.match(elements.grantStatus.textContent,/expired before dispatch/);
  assert.match(elements.grantReviewText.textContent,/expired before dispatch/);
  const count=requests.filter(x=>x.route==='/api/jobs').length;
  assert.equal(count,2,'an error does not automatically replay');
  listeners['prometheus-busy']({detail:{online:true,busy:true}});assert.ok(elements.grantRun.disabled);
  assert.ok(!elements.grantRefresh.disabled,'inspection remains available during another app job');
  listeners['prometheus-busy']({detail:{busy:false}});
  let resolve;reply=new Promise(done=>{resolve=done;});
  const pending=elements.grantReview.onclick();
  listeners['prometheus-auth-lost']();
  resolve({review_id:'late',scope_sha256:'late',target:'secret late value',seconds:300,uses:1});await pending;
  assert.ok(elements.grantIssue.disabled);assert.equal(elements.grantReviewText.textContent,'');
  assert.equal(elements.grantArguments.value,'{}');assert.equal(elements.grantRunId.value,'');
  assert.equal(elements.grantRows.children.length,0,'auth loss clears inventory');
  assert.ok(!elements.grantStatus.textContent.includes('secret late value'));
  console.log('Grant UI fixtures passed: exact review/create, invalidation, fast completion/error, no replay, busy inspection and auth-loss clearing. No live grants.');
})().catch(error=>{console.error(error);process.exitCode=1;});
