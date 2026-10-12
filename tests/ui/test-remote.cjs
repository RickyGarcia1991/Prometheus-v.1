'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {setup} = require('./test-workspace.cjs');
const asset = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
const script = fs.readFileSync(path.join(asset, 'remote.js'), 'utf8');
const markup = fs.readFileSync(path.join(asset, 'index.html'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const tools = [
  {name: 'terminal', description: '<script>Provider text only</script>', inputSchema: {type: 'object', required: ['deviceId', 'command'], properties: {
    deviceId: {type: 'string'}, command: {type: 'string', description: 'Exact terminal command'}, limit: {type: 'integer'}, wait: {type: 'boolean'}
  }}},
  {name: 'nested', description: 'Nested operation', inputSchema: {type: 'object', properties: {payload: {type: 'object'}}}}
];
const ready = {phase: 'ready', message: 'Connected.', selected_device_id: 'device-1', devices: [
  {id: 'device-1', name: 'Work computer', status: 'online'},
  {id: 'device-2', name: 'Same name <img src=x>', status: 'offline'},
  {id: 'device-3', name: 'Unknown machine', status: 'unknown'}
], tools, pending: null};
function harness(initial = ready) {
  const ui = setup();
  let current = clone(initial), failure = null;
  const calls = [], timers = new Map(); let nextTimer = 0;
  const ctx = {...ui.context, Date, setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    api: async (url, body) => {
      calls.push({url, ...(body ? {body: clone(body)} : {})});
      if (failure) { const value = failure; failure = null; throw value; }
      if (body?.action === 'prepare') current.pending = {id: 'preview-' + calls.length, device_id: current.selected_device_id,
        device_name: 'Work computer', tool: body.tool, arguments: {...body.arguments, deviceId: current.selected_device_id}, expires_at: Date.now() / 1000 + 60};
      if (body?.action === 'execute') { current.pending = null; current.phase = 'working'; current.message = 'Working.'; }
      if (body?.action === 'cancel') current.pending = null;
      if (body?.action === 'disconnect') current = {phase: 'disconnected', message: 'Disconnected.', devices: [], tools: [], pending: null};
      if (body?.action === 'connect') { current.phase = 'connecting'; current.message = 'Finish sign-in in the browser.'; }
      return clone(current);
    }
  };
  for (const match of script.matchAll(/element\('([^']+)'\)/g)) assert(ui.elements[match[1]], 'missing HTML element ' + match[1]);
  vm.runInNewContext(script, ctx, {filename: 'remote.js'});
  const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
  const open = async () => { ui.emit('prometheus-busy', {online: true, closed: false, busy: false}); ui.elements.tabRemote.click(); await flush(); };
  const choose = () => { ui.elements.remoteTool.value = 'terminal'; ui.elements.remoteTool.dispatch('change'); };
  const fields = () => ui.elements.remoteFields.children.filter(item => item.tagName === 'LABEL').map(item => item.children[0]);
  return {...ui, calls, timers, flush, open, choose, fields, set(value) { current = clone(value); }, fail(error) { failure = error; },
    async tick() { const [id, fn] = timers.entries().next().value || []; if (fn) { timers.delete(id); await fn(); await flush(); } }};
}

test('tab and account connection remain explicit; offline targets are disabled', async () => {
  const h = harness({phase: 'disconnected', devices: [], tools: [], pending: null}), e = h.elements;
  assert.equal(h.calls.length, 0, 'loading the page does not contact remote state or connect an account');
  assert(e.remoteConnect.disabled);
  await h.open();
  assert.deepEqual(h.calls, [{url: '/api/remote/state'}]);
  assert(!e.panelRemote.hidden && e.panelDocuments.hidden && e.panelMaintenance.hidden);
  await e.remoteConnect.click();
  assert.equal(h.calls.at(-1).body.action, 'connect');
  assert(e.remoteConnect.disabled && !e.remoteDisconnect.disabled);
  assert(e.remoteStatus.textContent.includes('sign-in'));
  h.set(ready); await h.tick();
  assert.equal(h.calls.at(-1).url, '/api/remote/state');
  assert(e.remoteDevice.options.find(option => option.value === 'device-2').disabled);
  assert(e.remoteDevice.options.find(option => option.value === 'device-3').disabled);
  assert(e.remoteDeviceInfo.textContent.includes('device-1'));
  e.remoteDevice.value = 'device-2'; e.remoteDevice.dispatch('change'); await h.flush();
  assert.equal(h.calls.at(-1).url, '/api/remote/state', 'offline target is not submitted');
  e.tabRemote.dispatch('keydown', {key: 'ArrowRight'});
  assert(!e.panelPermissions.hidden && e.panelRemote.hidden, 'keyboard reaches the new boundaries tab');
  e.tabPermissions.dispatch('keydown', {key: 'ArrowRight'});
  assert(!e.panelDocuments.hidden && e.panelPermissions.hidden, 'keyboard wraps across all five tabs');
  e.tabDocuments.dispatch('keydown', {key: 'End'});
  assert(!e.panelPermissions.hidden, 'End selects the last tab');
});

test('schema fields create exact preview and execution consumes only that preview once', async () => {
  const h = harness(), e = h.elements; await h.open(); h.choose();
  assert.equal(e.remoteToolDescription.textContent, '<script>Provider text only</script>');
  assert.equal(h.fields().length, 3, 'deviceId is injected by backend, never editable');
  await e.remotePrepare.click();
  assert(e.remoteStatus.textContent.includes('command'));
  assert.equal(h.calls.length, 1, 'missing required arguments never sent');
  const [command, limit, wait] = h.fields();
  command.value = 'echo "hello"'; limit.value = '0'; wait.value = 'false';
  await e.remotePrepare.click();
  assert.deepEqual(h.calls.at(-1).body, {action: 'prepare', tool: 'terminal', arguments: {command: 'echo "hello"', limit: 0, wait: false}});
  assert(e.remoteReviewTarget.textContent.includes('Work computer') && e.remoteReviewTarget.textContent.includes('device-1'));
  assert.deepEqual(JSON.parse(e.remotePreview.textContent), {command: 'echo "hello"', limit: 0, wait: false, deviceId: 'device-1'});
  assert(!e.remoteExecute.disabled);
  command.value = 'echo changed'; command.dispatch('input');
  assert(e.remoteExecute.disabled, 'editing invalidates the previous approval');
  await e.remotePrepare.click(); const preview = h.calls.length;
  const first = e.remoteExecute.click(); e.remoteExecute.click(); await first;
  const execute = h.calls.filter(call => call.body?.action === 'execute');
  assert.equal(execute.length, 1);
  assert.deepEqual(execute[0].body, {action: 'execute', pending_id: 'preview-' + preview});
  assert(e.remotePrepare.disabled && e.remoteExecute.disabled, 'working action locks new execution');
  h.set({...ready, result: {content: [{type: 'text', text: 'Completed <script>literal</script>'}]}}); await h.tick();
  assert.equal(e.remoteResult.children[1].textContent, 'Completed <script>literal</script>');
  assert(!e.remotePrepare.disabled);
});

test('advanced arguments preserve nested values and reject invalid JSON and device overrides', async () => {
  const h = harness(), e = h.elements; await h.open();
  e.remoteTool.value = 'nested'; e.remoteTool.dispatch('change');
  assert(e.remoteAdvanced.checked && !e.remoteJsonPanel.hidden);
  for (const value of ['[]', '{', 'null', '{"deviceId":"other"}']) {
    e.remoteArguments.value = value; await e.remotePrepare.click();
  }
  assert.equal(h.calls.length, 1);
  e.remoteArguments.value = '{"payload":{"files":["a.txt"],"empty":""}}'; await e.remotePrepare.click();
  assert.deepEqual(h.calls.at(-1).body.arguments, {payload: {files: ['a.txt'], empty: ''}});
  await e.remoteCancel.click(); assert.equal(h.calls.at(-1).body.action, 'cancel');
  assert(e.remoteReview.hidden);
});

test('observed and expired previews cannot execute, and local workbench controls cannot unlock them', async () => {
  const pending = {id: 'another-tab', device_id: 'device-1', device_name: 'Work computer', tool: 'terminal', arguments: {command: 'do something'}, expires_at: Date.now()/1000 + 60};
  const h = harness({...ready, pending}), e = h.elements; await h.open();
  assert(!e.remoteReview.hidden && e.remoteExecute.disabled);
  h.emit('prometheus-busy', {online: true, closed: false, busy: false});
  assert(e.remoteExecute.disabled && e.remoteArguments.disabled);
  h.choose(); h.fields()[0].value = 'echo 1'; await e.remotePrepare.click();
  assert(!e.remoteExecute.disabled);
  const actual = JSON.parse(e.remotePreview.textContent);
  h.set({...ready, pending: {...pending, id: 'expired', arguments: actual, expires_at: Date.now()/1000-1}});
  e.tabRemote.click(); await h.flush();
  assert(e.remoteExecute.disabled && e.remoteReviewExpiry.textContent.includes('expired'));
});

test('uncertain execution is not replayed and reconnect requires a fresh review', async () => {
  const h = harness(), e = h.elements; await h.open(); h.choose();
  h.fields()[0].value = 'echo once'; await e.remotePrepare.click();
  h.fail(new Error('Timed out')); await e.remoteExecute.click();
  assert(e.remoteExecute.disabled && e.remoteStatus.textContent.includes('uncertain'));
  await h.tick();
  assert.equal(h.calls.filter(call => call.body?.action === 'execute').length, 1);
  assert(e.remoteExecute.disabled);
  h.emit('prometheus-busy', {online: false, closed: false, busy: false});
  assert(e.remotePrepare.disabled && e.remoteDisconnect.disabled);
  h.emit('prometheus-busy', {online: true, closed: false, busy: false}); await h.flush();
  assert(e.remoteExecute.disabled, 'a reconnect cannot restore permission to execute');
});

test('a failed device switch restores the real target and read errors do not replay an action', async () => {
  const second = {id: 'device-other', name: 'Other online computer', status: 'online'};
  const h = harness({...ready, devices: [...ready.devices, second]}), e = h.elements;
  await h.open();
  h.fail(new Error('Device selection failed'));
  e.remoteDevice.value = second.id; await e.remoteDevice.dispatch('change');
  assert.equal(e.remoteDevice.value, 'device-1', 'a rejected selection cannot leave a misleading target selected');
  assert(e.remoteDeviceInfo.textContent.includes('device-1'));
  h.choose(); h.fields()[0].value = 'echo once'; await e.remotePrepare.click(); await e.remoteExecute.click();
  h.fail(new Error('Temporary state read failure')); await h.tick();
  assert(h.timers.size > 0, 'read-only status polling retries transient failures while working');
  await h.tick();
  assert.equal(h.calls.filter(call => call.body?.action === 'execute').length, 1);
});

test('remote output is bounded and only supported data images can render', async () => {
  const h = harness({...ready, result: {content: [
    {type: 'text', text: 'x'.repeat(100001)},
    {type: 'image', mimeType: 'image/svg+xml', data: 'PHN2Zz4='},
    {type: 'image', mimeType: 'image/png', data: 'aGVsbG8='},
    {type: 'resource_link', uri: 'javascript:alert(1)'}
  ]}}); await h.open();
  const children = h.elements.remoteResult.children;
  assert.equal(children.find(item => item.tagName === 'PRE').textContent.length, 100000);
  assert(children.some(item => item.textContent.includes('display limit')));
  assert.equal(children.filter(item => item.tagName === 'IMG').length, 1);
  assert(children.find(item => item.tagName === 'IMG').src.startsWith('data:image/png;base64,'));
  assert.equal(children.filter(item => item.tagName === 'A').length, 0);
  assert(children.some(item => item.textContent.includes('unavailable')));
  assert(!script.includes('innerHTML'));
  assert(markup.indexOf('/transport.js') < markup.indexOf('/remote.js'));
  assert(!script.includes('fetch('), 'all requests use the existing authenticated local api helper');
});
