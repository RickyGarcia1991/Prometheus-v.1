'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {setup} = require('./test-workspace.cjs');
function text(node) { return node.textContent + node.children.map(text).join(' '); }
function all(node) { return [node, ...node.children.flatMap(all)]; }
test('update controls dispatch bounded operations and disable while busy', async () => {
  const {elements:e, emit, submitted, result} = setup();
  assert(e.checkUpdates.disabled);
  emit('prometheus-busy', {busy:false, online:true, closed:false});
  for (const [id, payload] of [
    ['refreshUpdates',{mode:'component_updates'}], ['checkUpdates',{mode:'check_updates',enabled:true}],
    ['enableUpdates',{mode:'configure_updates',enabled:true}], ['disableUpdates',{mode:'configure_updates',enabled:false}]
  ]) {
    await e[id].click(); assert.deepEqual(submitted.at(-1),payload);
    result(payload.mode,{enabled:true, entries:[], due_count:0});
  }
  const before=submitted.length;
  emit('prometheus-busy',{busy:true});
  await e.checkUpdates.click(); assert.equal(submitted.length,before);
});
test('update status retains failure and pending activation and renders publisher content literally', () => {
  const {elements:e, result} = setup();
  result('component_updates',{enabled:true,due_count:2,entries:[
    {id:'pkg',label:'<img onerror=bad()>',category:'package',installed_revision:'1',available_revision:'2',status:'update_available',reason:'Tested signed release required',source_url:'https://pypi.org/project/pypdf/'},
    {id:'model',status:'check_failed',error:'Publisher unavailable'},
    {id:'archive',provider:'pinned',status:'check_unavailable',reason:'Pinned offline archive',source_url:'https://example.org/archive'}
  ]});
  const shown=text(e.componentUpdates);
  assert.match(shown, /update available/i); assert.match(shown,/Tested signed release required/);
  assert.match(shown,/Publisher unavailable/); assert.match(shown,/manual review/i);
  assert.match(shown,/<img onerror=bad\(\)>/);
  const link=all(e.componentUpdates).find(node=>node.tagName==='A'&&node.href==='https://pypi.org/project/pypdf/');
  assert.equal(link.href,'https://pypi.org/project/pypdf/');
  assert.equal(link.rel,'noopener noreferrer');
  result('configure_updates',{enabled:false,due_count:0,entries:[]});
  assert.match(text(e.componentUpdates),/paused/);
});

test('update groups prioritize real attention and keep pinned references distinct from failures',()=>{
  const {elements:e,result}=setup();
  result('component_updates',{enabled:true,due_count:0,entries:[
    {id:'app',provider:'pinned',category:'application',status:'check_unavailable',installed_revision:'Prometheus-v0.8.0-dev42-test',source_url:''},
    {id:'archive',provider:'pinned',category:'archive',status:'check_unavailable',source_url:'https://example.org/archive'},
    {id:'parser',provider:'pypi',status:'update_available',installed_revision:'1',available_revision:'2'},
    {id:'failed',provider:'github_release',status:'check_failed',error:'Publisher timed out'},
    {id:'changed-reference',provider:'github_commit',status:'upstream_changed',installed_revision:'',available_revision:'abc'},
    {id:'checked',provider:'pypi',status:'no_update'},
    {id:'baseline',provider:'github_commit',status:'baseline_recorded'},
    {id:'recovery',provider:'github_commit',status:'no_update',pending_recovery:true}
  ]});
  const groups=e.componentUpdates.children.filter(node=>node.tagName==='DETAILS');
  assert.match(groups[0].children[0].textContent,/Checks needing attention · 2/);
  assert.equal(groups[0].open,true);assert.match(text(groups[0]),/Publisher timed out/);assert.match(text(groups[0]),/Recovery pending/);
  assert.match(groups[1].children[0].textContent,/Updates awaiting review · 2/);assert.equal(groups[1].open,true);
  const manual=groups.find(group=>group.children[0].textContent.startsWith('Manual updates'));
  assert.equal(manual.open,false);assert.match(text(manual),/no configured feed/i);assert.match(text(manual),/manual review/i);
  assert.doesNotMatch(text(manual),/check unavailable|available not checked/i);
  assert.match(text(manual),/Prometheus-v0.8.0-dev42-test/);
  const checked=groups.find(group=>group.children[0].textContent.startsWith('Checked components'));
  assert.equal(checked.open,false);assert.match(checked.children[0].textContent,/· 2$/);
});
