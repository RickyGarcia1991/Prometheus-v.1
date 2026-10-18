'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {setup} = require('./test-workspace.cjs');
const source = fs.readFileSync(path.join(__dirname, '../../src/prometheus_assistant/ui_assets/runtime-settings.js'), 'utf8');
function text(element) { return [element.textContent, ...element.children.map(text)].join('\n'); }
function fixture() {
  const f = setup();
  f.context.session = 'current-chat';
  f.pending = null;
  f.context.api = async url => { assert.equal(url, '/api/approvals/pending'); return {approval:f.pending}; };
  vm.runInNewContext(source, f.context, {filename:'runtime-settings.js'});
  f.busy = busy => f.emit('prometheus-busy', {busy, online:true, closed:false});
  f.busy(false);
  return f;
}
const proposal = () => ({kind:'runtime_config', id:'review-one', fingerprint:'exact-review', session:'original-chat',
  review:{action:'Use CPU', reason:'Owner choice', benefit:'Avoid GPU discovery', effects:'Can be slower',
    current_backend:'automatic', proposed_backend:'cpu', duration:'Until changed'}});
(async () => {
  const f = fixture(), e = f.elements;
  e.runtimeBackend.value = 'cpu'; await e.reviewRuntimeSettings.click();
  assert.equal(f.submitted.at(-1).runtime_backend, 'cpu');
  assert.equal(f.submitted.at(-1).mode, 'runtime_config');
  f.result('runtime_config', {approval:proposal()}, {origin:'local'});
  assert(!e.runtimeSettingsReview.hidden);
  assert.match(text(e.runtimeSettingsReview), /Avoid GPU discovery/);
  assert.match(text(e.runtimeSettingsReview), /Can be slower/);
  e.runtimeBackend.value = 'automatic';
  await e.approveRuntimeSettings.click();
  assert.equal(f.submitted.at(-1).approval_fingerprint, 'exact-review');
  assert.equal(f.submitted.at(-1).session, 'original-chat');
  assert.equal(f.submitted.at(-1).runtime_backend, undefined);
  f.result('runtime_config', {approval_resolved:'review-one', runtime_settings:{backend:'cpu', revision:1}}, {origin:'local'});
  assert(e.runtimeSettingsReview.hidden);
  assert.equal(e.runtimeBackend.value, 'cpu');
  assert.match(e.runtimeSettingsStatus.textContent, /future launches/);
  assert.match(e.runtimeSettingsStatus.textContent, /not established/);

  f.result('runtime_config', {approval:proposal()}, {origin:'observed'});
  assert(e.runtimeSettingsReview.hidden, 'another tab cannot replace this review');
  f.pending = proposal(); await e.refreshRuntimeSettings.click();
  assert(!e.runtimeSettingsReview.hidden);
  await e.rejectRuntimeSettings.click();
  assert.equal(f.submitted.at(-1).approval_decision, 'reject');
  f.result('approval_decision', {}, {error:'Review expired', origin:'local'});
  assert(e.runtimeSettingsReview.hidden);
  assert.match(e.runtimeSettingsStatus.textContent, /Review expired/);

  f.pending = {kind:'project_edit'}; await e.refreshRuntimeSettings.click();
  assert.match(e.runtimeSettingsStatus.textContent, /Another change/);
  assert(e.approveRuntimeSettings.disabled);
  f.pending = null; await e.refreshRuntimeSettings.click();
  assert.equal(f.submitted.at(-1).mode, 'runtime_status');
  f.busy(true); assert(e.reviewRuntimeSettings.disabled);
  f.busy(false); f.result('runtime_config', {approval:proposal()}, {origin:'local'});
  f.emit('prometheus-auth-lost');
  assert(e.approveRuntimeSettings.disabled && e.runtimeSettingsReview.hidden);
  assert.match(e.runtimeSettingsStatus.textContent, /Unlock/);
  console.log('Runtime settings UI: exact approval, denial, refresh, stale and auth boundaries passed.');
})().catch(error => { console.error(error); process.exitCode=1; });
