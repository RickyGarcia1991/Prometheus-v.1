'use strict';
// DOM behavior fixtures only: no browser rendering, network or real owner session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assets = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
const markup = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(assets, 'permissions.js'), 'utf8');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.textContent = ''; this._value = ''; this.listeners = {}; this.disabled = false; this.hidden = false; }
  set value(value) { this._value = value; }
  get value() { return this._value || (this.tagName === 'select' ? this.children[0]?.value || '' : ''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.textContent = ''; this._value = ''; }
  addEventListener(name, action) { this.listeners[name] = action; }
}
const ids = [...markup.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)];
assert.equal(new Set(ids.map(item => item[2])).size, ids.length, 'unique DOM IDs');
const elements = Object.fromEntries(ids.map(item => [item[2], new Element(item[1])]));
const listeners = {}, requests = [];
const row = (id, capability, category, outcome) => ({id, capability, category, outcome, reason: 'Fixture reason', scope: 'Fixture scope', basis: 'runtime_evaluator', policy_source: 'guardrails.py', enforcement_point: 'Core', grant: {status: 'none_supplied', expires_in_seconds: null}, adjustable: false, requirements: 'Not probed'});
const fixture = {notice: 'Point-in-time visibility only', generated_at_utc: '2026-10-11T00:00:00Z', policy_revision: 'fixture-revision',
  coverage: {core_tools_evaluated: 2, evidence_tools_listed: 0, browser_modes_listed: 0, unmapped_surfaces: ['Partial inventory']},
  rows: [row('core.math.calculate', '<img src=x onerror=alert(1)>', 'application', 'allow'), row('identity.owner', 'Owner gate', 'authentication_integrity', 'not_configured')],
  preview_tools: [{worker: 'math', name: 'calculate', inputSchema: {required: ['expression']}}]};
let next = null;
async function api(route, body) { requests.push({route, body}); if (next) { const result = next; next = null; return result; } return route.endsWith('/preview') ? {outcome: 'allow', reason: 'Default', scope: 'Core only'} : fixture; }
vm.runInNewContext(source, {document: {getElementById: id => { assert.ok(elements[id], id); return elements[id]; }, createElement: tag => new Element(tag)}, window: {addEventListener: (name, handler) => { listeners[name] = handler; }}, api, console});
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  assert.equal(requests.length, 0, 'opening the app does not fetch automatically');
  assert.ok(elements.refreshPermissions.disabled);
  elements.panelPermissions.hidden = true;
  listeners['prometheus-busy']({detail: {online: true, busy: true}});
  assert.equal(elements.refreshPermissions.disabled, false, 'read-only view works while a job is busy');
  await elements.tabPermissions.listeners.click();
  assert.equal(requests[0].route, '/api/permissions');
  assert.match(elements.permissionCoverage.textContent, /Coverage is partial/);
  assert.match(elements.permissionCount.textContent, /2 of 2/);
  assert.equal(elements.permissionRows.children[0].children[0].textContent, '<img src=x onerror=alert(1)> · allow');
  assert.equal(elements.permissionRows.children[0].children[0].children.length, 0, 'untrusted text is not markup');
  elements.permissionCategory.value = 'authentication_integrity'; elements.permissionCategory.onchange();
  assert.equal(elements.permissionRows.children.length, 1);
  elements.permissionCategory.value = ''; elements.permissionSearch.value = 'no-such-boundary'; elements.permissionSearch.oninput();
  assert.equal(elements.permissionRows.children.length, 0);
  elements.permissionSearch.value = ''; elements.permissionSearch.oninput();
  elements.permissionArguments.value = '{broken'; await elements.previewPermission.onclick();
  assert.match(elements.permissionPreview.textContent, /valid JSON/);
  assert.equal(requests.length, 1);
  elements.permissionArguments.value = '{"expression":"1+2"}'; await elements.previewPermission.onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(requests[1])), {route: '/api/permissions/preview', body: {worker: 'math', tool: 'calculate', arguments: {expression: '1+2'}}});
  assert.match(elements.permissionPreview.textContent, /authorizes no execution/);
  elements.permissionArguments.oninput(); assert.equal(elements.permissionPreview.textContent, '');
  let resolve;
  next = new Promise(done => { resolve = done; });
  const pending = elements.refreshPermissions.onclick();
  assert.ok(elements.refreshPermissions.disabled);
  listeners['prometheus-busy']({detail: {online: false}});
  resolve(fixture); await pending;
  assert.equal(elements.permissionRows.children.length, 0, 'late response cannot restore stale rows after disconnect');
  assert.equal(elements.permissionSchema.textContent, '');
  elements.panelPermissions.hidden = false;
  listeners['prometheus-busy']({detail: {online: true}}); await flush();
  assert.equal(elements.permissionRows.children.length, 2);
  elements.ownerLock.listeners.click();
  assert.equal(elements.permissionRows.children.length, 0);
  assert.equal(elements.permissionArguments.value, '{}');
  next = Promise.reject(new Error('fixture connection error'));
  await elements.refreshPermissions.onclick();
  assert.match(elements.permissionStatus.textContent, /fixture connection error/);
  assert.equal(elements.permissionRows.children.length, 0);
  await elements.refreshPermissions.onclick();
  assert.equal(elements.permissionRows.children.length, 2, 'refresh recovers after an error');
  listeners['prometheus-busy']({detail: {closed: true}});
  assert.ok(elements.previewPermission.disabled);
  assert.equal(elements.permissionRows.children.length, 0);
  assert.ok(requests.every(item => ['/api/permissions', '/api/permissions/preview'].includes(item.route)));
  console.log('Permissions DOM fixtures passed: discovery, filters, text safety, preview, busy/error/lock/disconnect/stale-response states. No browser or physical acceptance.');
})().catch(error => { console.error(error); process.exitCode = 1; });
