'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const script=fs.readFileSync(path.join(__dirname,'../../src/prometheus_assistant/ui_assets/decision-center.js'),'utf8');
class Element{
 constructor(){this.children=[];this.textContent='';this.hidden=false;this.disabled=false;}
 append(item){this.children.push(item);}replaceChildren(){this.children=[];}scrollIntoView(){this.scrolled=true;}
 click(){if(!this.disabled)return this.onclick?.();}
 set innerHTML(value){throw Error('HTML forbidden');}
}
const snapshot=(extra={})=>({schema_version:1,generated_at:1000,read_only:true,items:[],sources:Object.fromEntries(['approvals','queue','activity','resources'].map(k=>[k,{state:'available'}])),counts:{critical:0,warning:0,info:0},...extra});
const item=(extra={})=>({id:'one',kind:'project',label:'Review pending',severity:'warning',state:'waiting',count:1,source:'approvals',freshness:'current',navigation:'project',...extra});
const settle=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
function fixture(){
 const elements=Object.fromEntries(['decisionCenter','decisionCenterStatus','decisionCenterList','decisionCenterRefresh','taskActivity','decisionHistory'].map(k=>[k,new Element()]));
 const events={},documentEvents={},timers=new Map(),calls=[],jumps=[];let now=1000000,next=0,response=snapshot();
 const document={hidden:false,getElementById:id=>elements[id],createElement:()=>new Element(),addEventListener:(k,fn)=>documentEvents[k]=fn};
 const context={document,window:{addEventListener:(k,fn)=>events[k]=fn,PrometheusWorkspace:{show:name=>jumps.push(name)}},Date:{now:()=>now},setTimeout:(fn,delay)=>{timers.set(++next,{fn,delay});return next;},clearTimeout:id=>timers.delete(id),api:async(...args)=>{calls.push(args);if(response instanceof Error)throw response;return typeof response==='function'?response():response;}};
 vm.runInNewContext(script,context);
 return {elements,calls,timers,jumps,set:value=>response=value,advance:n=>now+=n,
 emit:(detail={})=>events['prometheus-busy']({detail:{online:true,busy:false,closed:false,...detail}}),
 auth:()=>events['prometheus-auth-lost'](),hidden:value=>{document.hidden=value;documentEvents.visibilitychange();},
 async tick(){const [id,t]=[...timers].sort((a,b)=>a[1].delay-b[1].delay)[0];timers.delete(id);now+=t.delay;t.fn();await settle();}};
}
test('GET only, summary and explicit unavailable source; no HTML rendering',async()=>{
 const h=fixture();h.set(snapshot({items:[item({label:'<img onerror=bad>'})],sources:{...snapshot().sources,queue:{state:'unavailable'}}}));h.emit();await settle();
 assert.deepEqual(h.calls,[['/api/decision-center']]);assert.match(h.elements.decisionCenterStatus.textContent,/Sources unavailable: queue/);
 assert.match(h.elements.decisionCenterList.children[0].children[0].textContent,/<img onerror=bad>/);
 assert.equal(h.elements.decisionCenterList.children[0].children[0].children.length,0);
});
for(const value of [new Error('401'),{},snapshot({read_only:false}),snapshot({generated_at:900}),snapshot({items:Array(101).fill(item())}),snapshot({counts:{critical:NaN,warning:0,info:0}})])test('failed malformed or stale snapshot never reports empty success',async()=>{
 const h=fixture();h.set(value);h.emit();await settle();assert(h.elements.decisionCenterList.hidden);assert.match(h.elements.decisionCenterStatus.textContent,/unavailable|stale/);assert.doesNotMatch(h.elements.decisionCenterStatus.textContent,/No items need attention/);
});
test('disconnect and auth clear old rows and reject delayed response across reconnect',async()=>{
 const h=fixture();let resolve;h.set(()=>new Promise(r=>resolve=r));h.emit();await settle();h.emit({online:false});h.emit();resolve(snapshot({items:[item()]}));await settle();assert.equal(h.elements.decisionCenterList.children.length,0);
 h.advance(30000);h.set(snapshot({items:[item()]}));h.emit();await settle();assert.equal(h.elements.decisionCenterList.children.length,1);h.auth();assert.equal(h.elements.decisionCenterList.children.length,0);
});
test('polling throttles repeated status events and stops while hidden or busy',async()=>{
 const h=fixture();h.emit();await settle();for(let i=0;i<20;i++)h.emit();await settle();assert.equal(h.calls.length,1);assert.equal(h.timers.size,2);assert.equal(Math.min(...[...h.timers.values()].map(t=>t.delay)),30000);
 h.hidden(true);assert.equal(h.timers.size,0);h.advance(60000);h.hidden(false);await settle();assert.equal(h.calls.length,2);
 h.emit({busy:true});assert.equal(h.timers.size,0);h.advance(30000);h.emit({busy:true});await settle();assert.equal(h.calls.length,2);
});
test('bounded literal labels and navigation allowlist only invoke panel jumps',async()=>{
 const h=fixture();h.set(snapshot({items:['project','scheduled','remote','self_improvement','research','integrations','activity','javascript:bad','__proto__'].map(n=>item({label:'x'.repeat(500),navigation:n}))}));h.emit();await settle();
 const rows=h.elements.decisionCenterList.children;assert(rows[0].children[0].textContent.startsWith('x'.repeat(240)+' ·'));
 for(const row of rows)row.children[1]?.click();assert.deepEqual(h.jumps,['Code','Code','Remote','Maintenance','Maintenance','Integrations']);assert(h.elements.taskActivity.open);assert.equal(rows[7].children.length,1);assert.equal(rows[8].children.length,1);
 h.advance(61000);rows[0].children[1].click();assert.equal(h.jumps.length,6);
});
test('failed refresh removes previous rows',async()=>{const h=fixture();h.set(snapshot({items:[item()]}));h.emit();await settle();h.set(new Error('network'));await h.tick();assert.equal(h.elements.decisionCenterList.children.length,0);assert.match(h.elements.decisionCenterStatus.textContent,/unavailable/);});

test('age expiry clears rows even when a refresh never resolves',async()=>{const h=fixture();h.set(snapshot({items:[item()]}));h.emit();await settle();h.set(()=>new Promise(()=>{}));await h.tick();await h.tick();assert.equal(h.elements.decisionCenterList.children.length,0);assert.match(h.elements.decisionCenterStatus.textContent,/stale/);});


test('historical evidence opens only read-only history and unavailable source is explicit',async()=>{
 const h=fixture();h.set(snapshot({items:[item({source:'decisions',navigation:'history',state:'unknown'})],sources:{...snapshot().sources,decisions:{state:'partial'}}}));h.emit();await settle();
 assert.match(h.elements.decisionCenterStatus.textContent,/Sources unavailable: decisions/);
 h.elements.decisionCenterList.children[0].children[1].click();
 assert.equal(h.elements.decisionHistory.open,true);assert.deepEqual(h.jumps,[]);
 assert.deepEqual(h.calls,[['/api/decision-center']]);
});
