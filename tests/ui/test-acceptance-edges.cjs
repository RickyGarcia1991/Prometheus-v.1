'use strict';
// Additional isolated event-order contracts. No real fetch, model or device is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {setup} = require('./test-workspace.cjs');
const {AdaptiveDisplay} = require('../../src/prometheus_assistant/ui_assets/adaptive.js');
const asset = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
const response = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data});
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }
async function settle() { for (let i = 0; i < 50; i++) await Promise.resolve(); }
const saved = name => [{role: 'user', content: 'Question ' + name}, {role: 'assistant', content: 'Answer ' + name, label: 'Helper ' + name}];

async function fixture(voice=null) {
  const dom = setup({runWorkspace: false}), {elements: e, context, documentRoot} = dom;
  const requests = [], queued = new Map(), jobs = new Map(), documentEvents = {}, storage = new Map(), timers = [];
  let serial = 0;
  const server = {token: 'synthetic', status: {busy: false, job: null, state: 'idle', closing: false,
    hardware: {ram_gib: 16, available_ram_gib: 8, cpu_threads: 8, chat_ready: true}}};
  const screen = {now: 0, scheduled: 0, cancelled: 0};
  const reduced = {matches: false, addEventListener(_name, callback) { this.changed = callback; }};
  context.document.hidden = false;
  context.document.querySelectorAll = selector => documentRoot.querySelectorAll(selector);
  context.document.addEventListener = (name, callback) => { documentEvents[name] = callback; };
  context.window.dispatchEvent = event => dom.emit(event.type, event.detail);
  context.CustomEvent = class { constructor(type, value) { this.type = type; this.detail = value.detail; } };
  context.matchMedia = () => reduced;
  context.PrometheusAdaptive = {AdaptiveDisplay};
  if(voice)context.window.PrometheusVoice={LocalVoice:class{constructor(){return voice;}}};
  context.Image = class { constructor() { this.complete = false; this.naturalWidth = 0; } };
  context.performance = {now: () => screen.now};
  context.localStorage = {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)};
  context.requestAnimationFrame = () => ++screen.scheduled;
  context.cancelAnimationFrame = () => screen.cancelled++;
  context.setTimeout = (fn, delay) => { timers.push({fn, delay}); return timers.length; };
  context.AbortSignal = {timeout: () => ({})};
  context.location = {replace() {}};
  context.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({url, body});
    const queue = queued.get(url);
    if (queue?.length) return await queue.shift();
    if (url === '/api/bootstrap') return response({token: server.token, languages: ['Auto'], providers: [{id: 'crossref', title: 'Crossref'}]});
    if (url === '/api/status') { if (server.statusError) throw new Error('Synthetic disconnect'); return response(server.status); }
    if (url === '/api/sessions') return response(['alpha', 'beta'].map(session_id => ({session_id, created_at: '2026-10-10T00:00:00Z', turn_count: 2})));
    if (url.startsWith('/api/history/')) return response(saved(url.split('/').at(-1)));
    if (url === '/api/jobs') { const id = 'synthetic-' + (++serial); jobs.set(id, {state: 'running', partial: '', stage: 'Working', cancellable: true}); return response({id}); }
    if (url.startsWith('/api/job/')) return response(jobs.get(url.split('/').at(-1)));
    if (url === '/api/cancel') return response({cancel_requested: true});
    if (url === '/api/close') return response({closing: true, busy: false});
    throw new Error('Unexpected synthetic request ' + url);
  };
  const runtime = vm.createContext(context);
  for (const name of ['transport.js', 'app.js']) vm.runInContext(fs.readFileSync(path.join(asset, name), 'utf8'), runtime, {filename: name});
  await settle();
  async function select(name) { await vm.runInContext('refreshHistory()', runtime); e.history.value = name; return e.history.onchange(); }
  return {...dom, requests, queued, jobs, storage, server, screen, reduced, documentEvents, select,
    run: expression => vm.runInContext(expression, runtime)};
}

test('Text only remains authoritative after an obsolete audio unlock finishes', async () => {
 const ready=deferred();const voice={stop(){},unlock(){return ready.promise;}};const h=await fixture(voice);
 const pending=h.run('selectVoice(true)');await settle();await h.run('selectVoice(false)');
 ready.resolve();await pending;assert.equal(h.run('spokenMode'),false);
 assert.equal(h.elements.textMode.getAttribute('aria-pressed'),'true');assert.equal(h.elements.spokenMode.getAttribute('aria-pressed'),'false');
});

test('fresh startup never fetches or restores saved conversations', async () => {
 const h=await fixture();assert.equal(h.requests.some(r=>r.url==='/api/sessions'||r.url.startsWith('/api/history/')),false);assert.equal(h.run('session'),null);
});

test('newer history selection remains authoritative after an older request finishes late', async () => {
  const h = await fixture(), slow = deferred();
  h.queued.set('/api/history/alpha', [slow.promise]);
  const oldSelection = h.select('alpha'); await settle();
  await h.select('beta');
  slow.resolve(response(saved('alpha'))); await oldSelection;
  assert.equal(h.elements.workLabel.textContent, 'Helper beta', 'late alpha result must not replace the selected beta conversation');
  assert.equal(h.storage.get('prometheus.session'), 'beta');
  assert.equal(h.elements.messages.children.at(-1).querySelector('.text').textContent, 'Answer beta');
});

test('New conversation invalidates an outstanding history request', async () => {
  const h = await fixture(), slow = deferred();
  h.queued.set('/api/history/alpha', [slow.promise]);
  const oldSelection = h.select('alpha'); await settle();
  assert.equal(h.elements.newChat.disabled, false, 'the user can leave a pending saved-conversation view');
  await h.elements.newChat.click();
  slow.resolve(response(saved('alpha'))); await oldSelection;
  assert.equal(h.storage.has('prometheus.session'), false, 'late history must not reattach a conversation after New conversation');
  assert.equal(h.elements.messages.children.length, 0);
  assert.equal(h.elements.saveStatus.textContent, 'Ready · nothing sent');
});

test('late failure from an obsolete history request does not replace the current status', async () => {
  const h = await fixture(), slow = deferred();
  h.queued.set('/api/history/alpha', [slow.promise]);
  const oldSelection = h.select('alpha'); await settle();
  await h.select('beta'); const status = h.elements.notice.textContent;
  slow.resolve(response({error: 'Old conversation lookup failed'}, 500)); await oldSelection;
  assert.equal(h.elements.notice.textContent, status, 'obsolete selection errors must not override the current view');
  assert.equal(h.storage.get('prometheus.session'), 'beta');
});

test('an unfinished history selection cannot send a message into the previously open conversation', async () => {
  const h = await fixture(), slow = deferred(), e = h.elements;
  await h.select('alpha');
  h.queued.set('/api/history/beta', [slow.promise]);
  const selection = h.select('beta'); await settle();
  e.prompt.value = 'Follow up in beta';
  e.compose.onsubmit({preventDefault() {}}); await settle();
  assert.equal(h.requests.filter(r => r.url === '/api/jobs').length, 0, 'wait for the selected conversation before sending');
  assert.equal(e.prompt.value, 'Follow up in beta', 'waiting preserves the typed message');
  slow.resolve(response(saved('beta'))); await selection;
  e.compose.onsubmit({preventDefault() {}}); await settle();
  assert.equal(h.requests.findLast(r => r.url === '/api/jobs').body.session, 'beta');
});

test('failed current history selection restores the menu to the conversation still displayed', async () => {
  const h = await fixture();
  await h.select('alpha');
  h.queued.set('/api/history/beta', [Promise.resolve(response({error: 'Synthetic history failure'}, 500))]);
  await h.select('beta');
  assert.equal(h.elements.history.value, 'alpha', 'failed selection cannot leave the menu pointing at a different session');
  assert.equal(h.elements.workLabel.textContent, 'Helper alpha');
  assert.equal(h.elements.messages.children.at(-1).querySelector('.text').textContent, 'Answer alpha');
  assert.equal(h.elements.send.disabled, false, 'current conversation remains usable after the lookup error');
});

test('short saved histories report the actual visible message count without claiming hidden history', async () => {
  const h = await fixture();
  for (const count of [0, 1, 2, 39]) {
    const rows = Array.from({length: count}, (_, i) => ({role: i % 2 ? 'assistant' : 'user', content: 'Synthetic message ' + i}));
    h.queued.set('/api/history/alpha', [Promise.resolve(response(rows))]);
    await h.select('alpha');
    assert.equal(h.elements.notice.textContent, `Showing ${count} saved message${count === 1 ? '' : 's'}.`);
    assert.equal(h.elements.messages.children.length, count);
  }
});

test('a full history response identifies the display limit without asserting older messages exist', async () => {
  const h = await fixture();
  const rows = Array.from({length: 40}, (_, i) => ({role: i % 2 ? 'assistant' : 'user', content: 'Synthetic message ' + i}));
  h.queued.set('/api/history/alpha', [Promise.resolve(response(rows))]);
  await h.select('alpha');
  assert.equal(h.elements.messages.children.length, 40);
  assert.equal(h.elements.notice.textContent, 'Showing the latest 40 saved messages (display limit).');
});

test('restart, disconnect and new work invalidate obsolete history without clearing current messages', async () => {
  for (const event of ['restart', 'disconnect', 'work']) {
    const h = await fixture(), slow = deferred();
    await h.select('alpha');
    h.queued.set('/api/history/beta', [slow.promise]);
    const selection = h.select('beta'); await settle();
    if (event === 'restart') h.run('transport.onRestart()');
    else if (event === 'disconnect') { h.server.statusError = true; await h.run('status()'); }
    else await h.run("submitAction({mode:'capabilities'})");
    const currentLabel = h.elements.workLabel.textContent;
    slow.resolve(response(saved('beta'))); await selection;
    assert.equal(h.storage.get('prometheus.session'), 'alpha', event + ' prevents obsolete session replacement');
    assert.equal(h.elements.workLabel.textContent, currentLabel);
    assert.equal(h.elements.messages.children[0].querySelector('.text').textContent, 'Question alpha', 'current messages remain intact');
  }
});

test('explicit Still preference and hidden/reduced-motion transitions stop scheduled artwork', async () => {
  const h = await fixture(), e = h.elements;
  e.quality.value = 'still'; e.quality.onchange();
  assert.equal(e.tier.textContent, 'Still'); assert.equal(h.storage.get('prometheus.display'), 'still');
  const stoppedAt = h.screen.scheduled;
  await h.run('status()'); assert.equal(h.screen.scheduled, stoppedAt, 'status polling must preserve Still');
  e.quality.value = 'simple'; e.quality.onchange();
  for (const time of [30000, 60001, 90002, 120003]) { h.screen.now = time; await h.run('status()'); }
  assert.equal(e.tier.textContent, 'Simple', 'simple preference must cap effects after recovery');
  h.context.document.hidden = true; h.documentEvents.visibilitychange();
  assert.equal(e.tier.textContent, 'Still');
  h.context.document.hidden = false; h.reduced.matches = true; h.reduced.changed();
  assert.equal(e.tier.textContent, 'Still');
});

test('all conversation mode selections expose only their applicable source inputs', async () => {
  const h = await fixture(), e = h.elements;
  const cases = [
    ['chat', []], ['ask', ['sourceField']], ['vision', ['imageField']], ['coding', ['languageField']],
    ['library', ['profileField']], ['documents', []], ['calculate', []], ['research', ['providerField']],
    ['project', ['projectField']], ['home', ['homeKeyField']]
  ];
  const fields = ['sourceField', 'imageField', 'languageField', 'profileField', 'providerField', 'projectField', 'homeKeyField'];
  e.evidenceSource.value = 'documents';
  for (const [mode, visible] of cases) {
    e.mode.value = mode; e.mode.onchange();
    for (const field of fields) assert.equal(e[field].hidden, !visible.includes(field), mode + '/' + field);
  }
  assert.equal(h.requests.filter(r => r.url === '/api/jobs').length, 0, 'changing a menu never starts work');
});

test('source links keep exact destinations and source text remains literal across historical versions', () => {
  const h = setup(), e = h.elements;
  h.emit('prometheus-busy', {busy: false, online: true, closed: false});
  const url = 'https://docs.example/a%20b?rev=old&x=1#section-2';
  h.result('documents', {retrieval: 'keyword', results: [{title: '<script>literal title</script>', location: 'lines 8-9', source_url: url,
    excerpt: '<img src=x onerror=bad>', is_latest: false, version_id: 3}]});
  const card = e.documentResults.children.at(-1), link = card.querySelector('.source-link');
  assert.equal(card.children[0].textContent, '<script>literal title</script>');
  assert(card.children[1].textContent.includes('historical'));
  assert.equal(card.children[2].textContent, '<img src=x onerror=bad>');
  assert.equal(link.href, url); assert.equal(link.rel, 'noopener noreferrer');
});

test('disconnect disables draft mutations and reconnect retains reviewed draft safety', async () => {
  const h = setup(), e = h.elements;
  h.emit('prometheus-busy', {busy: false, online: true, closed: false});
  e.projectRoot.value = 'C:\\SyntheticProject'; await e.openProject.click();
  h.result('code_open', {project: e.projectRoot.value, files: [{path: 'a.py', size: 5}, {path: 'b.py', size: 5}], test_presets: []});
  e.codeFile.value = 'a.py'; e.codeFile.dispatch('change'); await e.readCode.click();
  h.result('code_read', {path: 'a.py', content: 'old\n', sha256: 'synthetic-before'});
  e.codeEditor.value = 'draft\n'; e.codeEditor.dispatch('input');
  h.emit('prometheus-busy', {busy: false, online: false, closed: false});
  assert(e.previewCode.disabled && e.applyCode.disabled && e.codeEditor.disabled);
  h.emit('prometheus-busy', {busy: false, online: true, closed: false});
  assert.equal(e.codeEditor.value, 'draft\n'); assert(!e.previewCode.disabled && e.applyCode.disabled);
  e.codeFile.value = 'b.py'; e.codeFile.dispatch('change');
  assert(e.previewCode.disabled && e.applyCode.disabled);
  assert.equal(e.codeEditor.value, 'draft\n');
});
