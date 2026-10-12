'use strict';
// Lightweight DOM contract test. No browser, rendering or network is involved.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const asset = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
const markup = fs.readFileSync(path.join(asset, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(asset, 'workspace.js'), 'utf8');

class Element {
  constructor(tagName = 'div', attrs = {}) {
    this.tagName = tagName.toUpperCase(); this.attrs = attrs; this.children = []; this.parentElement = null;
    this.listeners = {}; this.dataset = {}; this.disabled = 'disabled' in attrs; this.hidden = 'hidden' in attrs;
    this.checked = false; this.files = []; this.textContent = ''; this._value = attrs.value || ''; this.tabIndex = 0; this.style = {}; this.className = attrs.class || '';
    for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) this.dataset[key.slice(5)] = value;
    const classes = new Set((attrs.class || '').split(/\s+/));
    this.classList = {toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name)};
  }
  get options() { return this.children.filter(item => item.tagName === 'OPTION'); }
  get firstChild() { return this.children[0]; }
  get value() {
    if (this.tagName === 'SELECT') return this.options.find(item => item.value === this._value)?.value ?? this.options[0]?.value ?? '';
    return this._value;
  }
  set value(value) { this._value = this.tagName === 'TEXTAREA' ? String(value).replaceAll('\r\n', '\n') : String(value); }
  append(...items) { for (const item of items) { item.parentElement = this; this.children.push(item); } }
  replaceChildren(...items) { this.children = []; this._value = ''; this.append(...items); }
  add(item) { this.append(item); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
  getContext() { return null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  focus() { this.focused = true; }
  scrollIntoView(options) { this.scrollRequest = options; }
  closest(tag) { let item = this; while (item && item.tagName !== tag.toUpperCase()) item = item.parentElement; return item; }
  querySelectorAll(selector) {
    const all = [];
    const visit = item => { for (const child of item.children) { all.push(child); visit(child); } };
    visit(this);
    return all.filter(item => {
      if (selector === '[data-work-action]') return 'workAction' in item.dataset || 'work-action' in item.dataset || 'data-work-action' in item.attrs;
      if (selector === '[data-panel]') return 'panel' in item.dataset;
      if (selector === '[data-prompt]') return 'prompt' in item.dataset;
      if (selector.startsWith('.')) return item.className.split(/\s+/).includes(selector.slice(1));
      if (selector === 'input, select') return ['INPUT', 'SELECT'].includes(item.tagName);
      if (selector === '[role=tab][aria-selected=true]') return item.attrs.role === 'tab' && item.attrs['aria-selected'] === 'true';
      throw new Error('Unsupported test selector: ' + selector);
    });
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  dispatch(name, detail = {}) {
    const event = {key: '', preventDefault() { this.prevented = true; }, ...detail};
    for (const handler of this.listeners[name] || []) handler(event);
    if (typeof this['on' + name] === 'function') return this['on' + name](event);
  }
  click() { if (!this.disabled) return this.dispatch('click'); }
}
class Option extends Element {
  constructor(text, value) { super('option'); this.text = text; this.textContent = text; this.value = value; }
}

function setup({runWorkspace = true} = {}) {
  const elements = {}, documentRoot = new Element('document'), stack = [documentRoot];
  const voids = new Set(['meta', 'link', 'input', 'br', 'img', 'hr']);
  for (const match of markup.matchAll(/<\/?([a-z][a-z0-9]*)([^>]*?)>/gi)) {
    const closing = match[0].startsWith('</'), tag = match[1].toLowerCase();
    if (closing) { if (stack.at(-1).tagName === tag.toUpperCase()) stack.pop(); continue; }
    const attrs = {};
    for (const entry of match[2].matchAll(/([\w-]+)(?:="([^"]*)"|='([^']*)'|=([^\s]+))?/g)) attrs[entry[1]] = entry[2] ?? entry[3] ?? entry[4] ?? '';
    const node = tag === 'option' ? new Option('', attrs.value || '') : new Element(tag, attrs);
    node.attrs = attrs;
    if (attrs.id) { assert(!elements[attrs.id], 'duplicate HTML id: ' + attrs.id); elements[attrs.id] = node; }
    stack.at(-1).append(node);
    if (!voids.has(tag)) stack.push(node);
  }
  for (const match of script.matchAll(/element\('([^']+)'\)/g)) assert(elements[match[1]], 'script references missing HTML id ' + match[1]);
  const events = {}, submitted = [];
  const emit = (name, detail) => { for (const fn of events[name] || []) fn({detail}); };
  const result = (action, details, extra = {}) => emit('prometheus-result', {action, details, ...extra});
  elements.provider.add(new Option('Crossref', 'crossref')); elements.provider.add(new Option('NASA', 'nasa'));
  const context = {
    document: {getElementById: id => elements[id] || null, createElement: tag => new Element(tag)}, Option, URL,
    window: {addEventListener(name, fn) { (events[name] ||= []).push(fn); }},
    MutationObserver: class { observe() {} },
    FileReader: class { readAsDataURL(file) { this.result = 'data:application/octet-stream;base64,' + Buffer.from(file.content || '').toString('base64'); queueMicrotask(() => this.onload()); } },
    submitAction: async body => { submitted.push(JSON.parse(JSON.stringify(body))); return true; }
  };
  if (runWorkspace) vm.runInNewContext(script, context, {filename: 'workspace.js'});
  return {elements, submitted, emit, result, context, documentRoot};
}
module.exports = {setup, Element, Option};
if (require.main === module) {
const state = setup(), {elements: e, submitted, emit, result} = state;
function last() { return submitted.at(-1); }
function input(id, value) { e[id].value = value; e[id].dispatch('input'); }
function busy(value) { emit('prometheus-busy', {busy: value, online: true, closed: false}); }

(async () => {
  assert(markup.indexOf('/transport.js') < markup.indexOf('/app.js'), 'transport must load before app');
  assert(markup.indexOf('/app.js') < markup.indexOf('/workspace.js'), 'workspace must load after app');
  assert(e.cancelRequest.hidden, 'cancellation control starts hidden');
  assert(e.openProject.disabled, 'no mutations while disconnected');
  busy(false);
  await e.openWorkbench.click(); assert.equal(e.workbench.hidden, false);
  await e.tabCode.click(); assert.equal(e.panelCode.hidden, false); assert.equal(e.panelDocuments.hidden, true);
  e.tabCode.dispatch('keydown', {key: 'ArrowRight'}); assert.equal(e.panelMaintenance.hidden, false);
  e.tabMaintenance.dispatch('keydown', {key: 'End'}); assert.equal(e.panelPermissions.hidden, false);
  e.tabPermissions.dispatch('keydown', {key: 'ArrowLeft'}); assert.equal(e.panelRemote.hidden, false);
  e.tabRemote.dispatch('keydown', {key: 'ArrowRight'}); assert.equal(e.panelPermissions.hidden, false);
  e.tabPermissions.dispatch('keydown', {key: 'Home'}); assert.equal(e.panelDocuments.hidden, false);
  await e.closeWorkbench.click(); assert.equal(e.workbench.hidden, true);
  for (const [control, mode] of [['listDocuments', 'document_list'], ['indexDocuments', 'document_index'],
    ['checkReadiness', 'maintenance'], ['checkSources', 'check_sources']]) {
    await e[control].click(); assert.deepEqual(last(), {mode}, control + ' dispatches its supported backend operation');
    result(mode, {});
  }
  const initialRequests = submitted.length;
  await e.openProject.click(); assert.equal(submitted.length, initialRequests, 'empty project must not be submitted');
  input('projectRoot', 'C:\\Projects\\demo'); await e.openProject.click();
  assert.deepEqual(last(), {mode: 'code_open', project: 'C:\\Projects\\demo'});
  result('code_open', {project: 'C:\\Projects\\demo', files: [{path: 'app.py', size: 9}, {path: 'other.py', size: 9}], test_presets: [{id: 'python-unittest', label: 'Python unittest', available: true}]});
  e.codeFile.value = 'app.py'; e.codeFile.dispatch('change');
  assert(!e.readCode.disabled, 'selecting a listed file enables Open file');
  await e.readCode.click(); assert.equal(last().file, 'app.py');
  result('code_read', {path: 'app.py', content: 'value = 1\r\n', sha256: 'old-sha'});
  assert.equal(e.codeEditor.value, 'value = 1\n');
  input('codeEditor', 'value = 2\n'); assert(!e.previewCode.disabled); assert(e.applyCode.disabled);
  await e.previewCode.click();
  assert.deepEqual(last(), {mode: 'code_preview', project: 'C:\\Projects\\demo', file: 'app.py', content: 'value = 2\n', expected_sha256: 'old-sha'});
  result('code_preview', {path: 'app.py', before_sha256: 'old-sha', changed: true, diff: '-value = 1\n+value = 2'});
  assert(!e.applyCode.disabled, 'matching diff permits Apply');
  const beforeSelection = submitted.length;
  e.codeFile.value = 'other.py'; e.codeFile.dispatch('change');
  assert(e.applyCode.disabled && e.previewCode.disabled, 'a different selection cannot apply the opened file preview');
  assert.equal(e.codeEditor.value, 'value = 2\n', 'file selection preserves the unapplied draft');
  assert(e.codeFileStatus.textContent.includes('app.py'), 'editor explicitly identifies the opened file');
  await e.applyCode.click(); await e.readCode.click();
  assert.equal(submitted.length, beforeSelection, 'changing selection never applies or discards the old draft');
  e.codeFile.value = 'app.py'; e.codeFile.dispatch('change');
  assert(!e.previewCode.disabled && e.applyCode.disabled, 'restoring selection requires a fresh preview');
  await e.previewCode.click();
  result('code_preview', {path: 'app.py', before_sha256: 'old-sha', changed: true, diff: '-value = 1\n+value = 2'});
  assert(!e.applyCode.disabled);
  input('codeEditor', 'value = 3\n'); assert(e.applyCode.disabled, 'editing after preview invalidates Apply');
  result('code_open', {project: 'C:\\Other', files: []}, {origin: 'observed'});
  result('code_read', {path: 'other.py', content: 'replace draft', sha256: 'other'}, {origin: 'observed'});
  result('code_undo', {undone: true});
  assert.equal(e.codeEditor.value, 'value = 3\n', 'unrequested project results must preserve the editor draft');
  assert.equal(e.projectRoot.value, 'C:\\Projects\\demo');
  const count = submitted.length;
  await e.readCode.click(); await e.openProject.click();
  assert.equal(submitted.length, count, 'switching file/project preserves unapplied editor text');
  assert.equal(e.codeEditor.value, 'value = 3\n');
  busy(true); assert(e.previewCode.disabled); assert(e.codeEditor.disabled); assert(e.runCodeTests.disabled);
  busy(false); await e.previewCode.click();
  result('code_preview', {path: 'app.py', before_sha256: 'old-sha', changed: true, diff: '-value = 1\n+value = 3'});
  await e.applyCode.click();
  assert.equal(last().mode, 'code_apply'); assert.equal(last().content, 'value = 3\n');
  result('code_apply', {}, {error: 'File changed since it was opened.'});
  assert.equal(e.codeEditor.value, 'value = 3\n', 'conflict retains typed content');
  assert(e.applyCode.disabled, 'conflict invalidates the previously reviewed edit');
  assert(e.workspaceStatus.textContent.includes('changed since'));
  // Re-preview with current original remains explicit; backend rechecks every apply.
  await e.previewCode.click(); result('code_preview', {path: 'app.py', before_sha256: 'old-sha', changed: true, diff: 'reviewed'});
  await e.applyCode.click(); result('code_apply', {path: 'app.py', sha256: 'new-sha', edit_id: 'edit-1', changed: true});
  assert(e.previewCode.disabled, 'successful apply resets editor baseline');
  assert(!e.undoCode.disabled, 'successful apply retains undo identifier');
  await e.undoCode.click(); assert.equal(last().edit_id, 'edit-1');
  result('code_undo', {}, {error: 'Undo would overwrite newer work.'});
  assert.equal(e.codeEditor.value, 'value = 3\n', 'undo conflict does not clear editor');
  await e.undoCode.click(); result('code_undo', {path: 'app.py', sha256: 'old-sha', undone: true});
  assert(e.codeEditor.disabled, 'undo requests a fresh file read');
  e.testTarget.value = 'tests'; await e.runCodeTests.click();
  assert.deepEqual(last(), {mode: 'code_tests', project: 'C:\\Projects\\demo', preset: 'python-unittest', target: 'tests'});
  emit('prometheus-partial', {text: 'test is running'}); assert.equal(e.codeTestOutput.textContent, 'test is running');
  result('code_tests', {exit_code: 1, duration_seconds: 0.5, argv: ['python', '-m', 'unittest'], output: 'failure', cancelled: false, timed_out: false});
  assert(e.codeTestOutput.textContent.startsWith('Failed'), 'nonzero exit is not reported as passing');
  e.watchProvider.value = 'nasa'; e.watchQuery.value = 'public lunar missions';
  await e.enableWatch.click(); assert.deepEqual(last(), {mode: 'watch_source', provider: 'nasa', prompt: 'public lunar missions', enabled: true});
  await e.disableWatch.click(); assert.equal(last().enabled, false);
  await e.benchmarkHelpers.click(); assert.deepEqual(last(), {mode: 'model_benchmark'});
  result('model_benchmark', {tested: 1, latency_seconds: 2});
  assert(e.benchmarkResults.children[0].textContent.includes('latency_seconds'));
  busy(true); assert(e.runHealthCheck.disabled, 'health check shares the single-job lock');
  busy(false); await e.runHealthCheck.click(); assert.deepEqual(last(), {mode: 'health_check'});
  result('health_check', {checked_utc: '2026-10-10T00:00:00Z', counts: {pass: 1, fail: 1, blocked: 1, untested: 1}, checks: [
    {title: 'Code files', status: 'fail', summary: '<script>literal</script>', next_step: 'Review the intended release.'},
    {title: 'Available RAM', status: 'blocked', summary: 'Close unused applications.'},
    {title: 'Microphone acceptance', status: 'untested', summary: 'No recording was performed by this check.'},
    {title: 'Runtime', status: 'pass', summary: 'Runtime files are present.'}
  ]});
  assert(e.healthResults.children[0].textContent.includes('1 failed'));
  assert.equal(e.healthResults.children[1].children[0].textContent, 'Needs attention · Code files');
  assert.equal(e.healthResults.children[1].children[1].textContent, '<script>literal</script>', 'health output is literal text');
  assert(e.healthResults.children[1].children[2].textContent.includes('Review the intended release'));
  assert(e.healthResults.children[3].children[0].textContent.startsWith('Not run by this check'));
  for (const [index, status] of ['fail', 'blocked', 'untested', 'pass'].entries()) {
    assert.equal(e.healthResults.children[index + 1].getAttribute('data-health-status'), status);
    assert.equal(e.healthResults.children[index + 1].className, 'workspace-card health-' + status);
  }
  assert.match(e.workspaceStatus.textContent, /1 failed, 1 blocked/);
  assert.match(e.workspaceStatus.textContent, /Review the cards needing attention/);
  result('health_check', {counts: {pass: 6, untested: 2}, checks: [
    {title: 'Physical acceptance', status: 'untested'}, {title: 'Unknown check', status: 'future_status'}
  ]});
  assert.equal(e.workspaceStatus.textContent, 'Health check complete: 6 passed, 0 failed, 0 blocked. 2 checks were not run by this passive check.');
  assert(e.healthResults.children.slice(1).every(card => card.className === 'workspace-card health-untested'), 'untested and unknown results never receive passing styling');
  e.documentQuery.value = 'source history'; e.documentHistory.checked = true;
  await e.searchDocuments.click(); assert.deepEqual(last(), {mode: 'documents', prompt: 'source history', include_history: true});
  e.documentFile.files = [{name: 'large.txt', size: 8 * 1024 * 1024 + 1, content: ''}]; e.documentFile.dispatch('change');
  const beforeLarge = submitted.length; await e.importDocument.click(); assert.equal(submitted.length, beforeLarge, 'oversize import rejected before upload');
  e.documentFile.files = [{name: 'example.md', size: 4, content: 'test'}]; e.documentFile.dispatch('change');
  e.documentUrl.value = 'https://example.org/doc'; e.documentDate.value = '2026-10-09';
  await e.importDocument.click();
  assert.deepEqual(last(), {mode: 'document_import', content: 'dGVzdA==', name: 'example.md', source_url: 'https://example.org/doc', source_date: '2026-10-09', document_id: ''});
  result('document_import', {});
  for (const name of ['slides.pptx', 'inventory.xlsx', 'SLIDES.PPTX', 'INVENTORY.XLSX']) {
    e.documentFile.files = [{name, size: 4, content: 'test'}]; e.documentFile.dispatch('change');
    const beforeImport = submitted.length;
    await e.importDocument.click();
    assert.equal(submitted.length, beforeImport + 1, name + ' reaches the document importer');
    assert.deepEqual(last(), {mode: 'document_import', content: 'dGVzdA==', name, source_url: 'https://example.org/doc', source_date: '2026-10-09', document_id: ''});
    result('document_import', {});
  }
  for (const name of ['macros.xlsm', 'legacy.ppt', 'program.exe']) {
    e.documentFile.files = [{name, size: 4, content: 'test'}]; e.documentFile.dispatch('change');
    const beforeImport = submitted.length;
    await e.importDocument.click();
    assert.equal(submitted.length, beforeImport, name + ' remains unsupported');
  }
  e.backupDestination.value = 'C:\\Backups'; await e.createBackup.click(); assert.deepEqual(last(), {mode: 'backup', destination: 'C:\\Backups'});
  e.restoreBackupPath.value = 'C:\\Backups\\saved'; e.restoreDestination.value = 'C:\\Backups\\new-restore'; await e.restoreCheck.click();
  assert.deepEqual(last(), {mode: 'restore_check', backup_path: 'C:\\Backups\\saved', destination: 'C:\\Backups\\new-restore'});
  const sourceUrl = 'https://publisher.example/docs/pinned/revision?part=123&view=full#pinout';
  const sourceRow = {title: 'Pinned interface guide', document_id: 'guide', version_id: 1, is_latest: true,
    source_url: sourceUrl, excerpt: 'Exact source excerpt', location: 'lines 10-20'};
  result('document_list', {documents: [sourceRow]});
  result('documents', {results: [sourceRow]});
  for (const holder of [e.documentList, e.documentResults]) {
    const links = holder.querySelectorAll('.source-link');
    assert.equal(links.length, 1, 'document list and search expose clickable source links');
    assert.equal(links[0].href, sourceUrl); assert.equal(links[0].title, sourceUrl);
    assert.equal(links[0].textContent, 'Source · publisher.example');
    assert.equal(links[0].target, '_blank'); assert.equal(links[0].rel, 'noopener noreferrer');
  }
  for (const unsafe of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>',
    'https://name:secret@publisher.example/path', 'not a URL']) {
    result('document_list', {documents: [{...sourceRow, source_url: unsafe}]});
    result('documents', {results: [{...sourceRow, source_url: unsafe}]});
    for (const holder of [e.documentList, e.documentResults]) {
      assert.equal(holder.querySelectorAll('.source-link').length, 0, 'unsafe targets must stay literal');
      assert.equal(holder.querySelectorAll('.source-location')[0].textContent, unsafe);
    }
  }
  console.log('Workspace DOM contract and 35+ behavior assertions passed; no browser or network used.');
})().catch(error => { console.error(error); process.exitCode = 1; });
}
