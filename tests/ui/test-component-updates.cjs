'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {setup} = require('./test-workspace.cjs');
function text(node) { return node.textContent + node.children.map(text).join(' '); }
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
    {id:'archive',status:'check_unavailable',reason:'Pinned offline archive'}
  ]});
  const shown=text(e.componentUpdates);
  assert.match(shown, /update available/); assert.match(shown,/Tested signed release required/);
  assert.match(shown,/Publisher unavailable/); assert.match(shown,/check unavailable/);
  assert.match(shown,/<img onerror=bad\(\)>/);
  const card=e.componentUpdates.children[1], link=card.children.at(-1).children[0];
  assert.equal(link.href,'https://pypi.org/project/pypdf/');
  assert.equal(link.rel,'noopener noreferrer');
  result('configure_updates',{enabled:false,due_count:0,entries:[]});
  assert.match(text(e.componentUpdates),/paused/);
});
