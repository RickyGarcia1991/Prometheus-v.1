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
    document: {getElementById: id => elements[id] || null, createElement: tag => new Element(tag)}, Option,
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
  await e.openProject.click(); assert.equal(submitted.length, 0, 'empty project must not be submitted');
  input('projectRoot', 'C:\\Projects\\demo'); await e.openProject.click();
  assert.deepEqual(last(), {mode: 'code_open', project: 'C:\\Projects\\demo'});
  result('code_open', {project: 'C:\\Projects\\demo', files: [{path: 'app.py', size: 9}], test_presets: [{id: 'python-unittest', label: 'Python unittest', available: true}]});
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
  input('codeEditor', 'value = 3\n'); assert(e.applyCode.disabled, 'editing after preview invalidates Apply');
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
  e.documentQuery.value = 'source history'; e.documentHistory.checked = true;
  await e.searchDocuments.click(); assert.deepEqual(last(), {mode: 'documents', prompt: 'source history', include_history: true});
  e.documentFile.files = [{name: 'large.txt', size: 8 * 1024 * 1024 + 1, content: ''}]; e.documentFile.dispatch('change');
  const beforeLarge = submitted.length; await e.importDocument.click(); assert.equal(submitted.length, beforeLarge, 'oversize import rejected before upload');
  e.documentFile.files = [{name: 'example.md', size: 4, content: 'test'}]; e.documentFile.dispatch('change');
  e.documentUrl.value = 'https://example.org/doc'; e.documentDate.value = '2026-10-09';
  await e.importDocument.click();
  assert.deepEqual(last(), {mode: 'document_import', content: 'dGVzdA==', name: 'example.md', source_url: 'https://example.org/doc', source_date: '2026-10-09', document_id: ''});
  e.backupDestination.value = 'C:\\Backups'; await e.createBackup.click(); assert.deepEqual(last(), {mode: 'backup', destination: 'C:\\Backups'});
  e.restoreBackupPath.value = 'C:\\Backups\\saved'; e.restoreDestination.value = 'C:\\Backups\\new-restore'; await e.restoreCheck.click();
  assert.deepEqual(last(), {mode: 'restore_check', backup_path: 'C:\\Backups\\saved', destination: 'C:\\Backups\\new-restore'});
  console.log('Workspace DOM contract and 35+ behavior assertions passed; no browser or network used.');
})().catch(error => { console.error(error); process.exitCode = 1; });
}
