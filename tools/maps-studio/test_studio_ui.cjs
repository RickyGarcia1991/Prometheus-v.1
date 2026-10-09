'use strict';

// Offline contract tests: evaluate the shipped script and its actual HTML IDs.
// The only fakes are DOM/canvas, fetch, file decoding and timers. No browser/server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const root = __dirname;
const source = fs.readFileSync(process.env.STUDIO_UI_SOURCE || path.join(root, 'studio.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const realCatalog = JSON.parse(fs.readFileSync(path.join(root, 'catalog.json'), 'utf8').replace(/^\uFEFF/, ''));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const research = title => ({results: [{title, url: 'https://images.nasa.gov/details/' + title, description: 'fixture'}], source: 'NASA', retrieved_utc: '2026-10-09T00:00:00Z', note: 'fixture'});
const report = (file, truncated = false) => ({file, checked_utc: '2026-10-09T01:00:00Z', scope: 'Heuristic fixture', findings: [], truncated});
const imageFile = name => ({name, type: 'image/png', size: 1024});
const bitmap = (width = 120, height = 80) => ({width, height, closed: false, close() { this.closed = true; }});
const codeFile = (name, text = 'pass') => ({name, size: text.length, text: async () => text});

function harness(initial = {}) {
  const h = {elements: new Map(), all: [], requests: [], queues: new Map(), downloads: [], opened: [], timers: new Map(), blobs: new Map(), canvasCalls: []};
  let timerId = 0;
  class Element {
    constructor(tag, attributes = '') {
      this.tag = tag;
      this.attributes = {};
      for (const match of attributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) this.attributes[match[1]] = match[2] ?? '';
      this.id = this.attributes.id;
      this.value = this.attributes.value || '';
      this.hidden = Object.hasOwn(this.attributes, 'hidden');
      this.disabled = Object.hasOwn(this.attributes, 'disabled');
      this.dataset = this.attributes['data-tab'] ? {tab: this.attributes['data-tab']} : {};
      this.files = [];
      this.events = new Map();
      this.children = [];
      this._text = '';
      this.width = Number(this.attributes.width || 300);
      this.height = Number(this.attributes.height || 150);
      this.context = {
        drawImage: (...args) => h.canvasCalls.push({operation: 'drawImage', canvas: this, args}),
        fillRect: (...args) => h.canvasCalls.push({operation: 'fillRect', canvas: this, args}),
        translate: (...args) => h.canvasCalls.push({operation: 'translate', canvas: this, args}),
        rotate: (...args) => h.canvasCalls.push({operation: 'rotate', canvas: this, args})
      };
      h.all.push(this);
      if (this.id) h.elements.set(this.id, this);
    }
    get textContent() { return this._text + this.children.map(child => child.textContent || '').join(' '); }
    set textContent(value) { this._text = String(value); this.children = []; }
    append(...children) { this.children.push(...children); }
    prepend(...children) { this.children.unshift(...children); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; delete this[name]; }
    addEventListener(name, callback) {
      if (!this.events.has(name)) this.events.set(name, []);
      this.events.get(name).push(callback);
    }
    async dispatch(name) { await Promise.all((this.events.get(name) || []).map(callback => callback({target: this}))); }
    click() {
      if (this.download) { h.downloads.push({name: this.download, blob: h.blobs.get(this.href)}); return Promise.resolve(); }
      return this.disabled ? Promise.resolve() : this.dispatch('click');
    }
    getContext() { return this.context; }
    toBlob(callback, type) {
      if (h.exportImage) h.exportImage({canvas: this, callback, type});
      else callback(new Blob(['fixture'], {type}));
    }
  }
  for (const match of html.matchAll(/<(button|input|select|canvas|section|p|div|iframe)[\s>]([^>]*?)>/g)) {
    // The tag regex consumes one separator; attributes retain their names.
    new Element(match[1], match[2]);
  }
  // Select defaults are supplied by the actual first option in the shipped HTML.
  for (const match of html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>[\s\S]*?<option\b[^>]*value="([^"]*)"/g)) h.elements.get(match[1]).value = match[2];
  h.$ = id => { assert.ok(h.elements.has(id), 'Unknown actual HTML id: ' + id); return h.elements.get(id); };
  h.queue = (url, fixture) => {
    if (!h.queues.has(url)) h.queues.set(url, []);
    h.queues.get(url).push(fixture);
  };
  for (const [url, fixture] of Object.entries(initial)) h.queue(url, fixture);
  h.fetch = (url, options = {}) => {
    const record = {url, options, body: options.body ? JSON.parse(options.body) : null};
    h.requests.push(record);
    const queue = h.queues.get(url) || [];
    const fixture = queue.shift() || (url === '/api/bootstrap' ? {data: {token: 'offline-fixture'}} :
      url === '/catalog.json' ? {data: realCatalog} : url === '/api/close' ? {data: {closed: true}} : null);
    assert.ok(fixture, 'No offline response fixture for ' + url);
    return new Promise((resolve, reject) => {
      const aborted = () => { if (!fixture.ignoreAbort) reject(new DOMException('Aborted', 'AbortError')); };
      options.signal?.addEventListener('abort', aborted, {once: true});
      if (options.signal?.aborted) aborted();
      Promise.resolve(fixture.gate ? fixture.gate.promise : fixture.data).then(data => {
        if (fixture.error) reject(fixture.error);
        else resolve({ok: fixture.ok !== false, json: async () => data});
      }, reject);
    });
  };
  class FakeURL extends URL {
    static createObjectURL(blob) { const url = 'blob:fixture-' + h.blobs.size; h.blobs.set(url, blob); return url; }
    static revokeObjectURL() {}
  }
  const document = {
    getElementById: h.$,
    createElement: tag => new Element(tag),
    querySelectorAll(selector) {
      if (selector === '[data-tab]') return h.all.filter(element => element.dataset.tab);
      if (selector === '.panel') return h.all.filter(element => element.attributes.class === 'panel');
      const tags = selector.split(',').map(tag => tag.trim());
      return h.all.filter(element => tags.includes(element.tag));
    }
  };
  h.decode = async () => bitmap();
  h.runTimers = delay => {
    for (const [id, timer] of [...h.timers]) {
      if (timer.delay === delay) { h.timers.delete(id); timer.callback(); }
    }
  };
  h.document = document;
  vm.runInNewContext(source, {
    document, fetch: h.fetch, URL: FakeURL, URLSearchParams, Blob, AbortController,
    createImageBitmap: file => h.decode(file),
    setTimeout(callback, delay) { const id = ++timerId; h.timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { h.timers.delete(id); },
    window: {open: (...args) => h.opened.push(args)}, console
  }, {filename: 'studio.js'});
  return h;
}

async function ready(initial) { const h = harness(initial); await tick(); return h; }
async function selectImage(h, file = imageFile('photo.png')) { h.$('imageFile').files = [file]; await h.$('imageFile').dispatch('change'); }
async function selectCode(h, file) { h.$('codeFile').files = [file]; await h.$('codeFile').dispatch('change'); }
async function chooseRegion(h, value) { h.$('regions').value = value; await h.$('regions').dispatch('change'); }
async function regions(h) {
  h.queue('/api/regions', {data: {regions: [{id: 'a', name: 'Region A'}, {id: 'b', name: 'Region B'}], retrieved_utc: 'fixture'}});
  await h.$('loadRegions').click();
}
const freshness = id => ({id, name: 'Region ' + id.toUpperCase(), url: 'https://download.geofabrik.de/' + id + '-latest.osm.pbf', bytes: '1048576', checked_utc: 'fixture', note: 'fixture'});

test('every shipped button has its action binding; all six navigation buttons work', async () => {
  const h = await ready();
  const buttons = h.document.querySelectorAll('button');
  assert.ok(buttons.length >= 25);
  for (const button of buttons) assert.ok(button.events.get('click')?.length, button.id || button.dataset.tab);
  for (const button of h.document.querySelectorAll('[data-tab]')) {
    await button.click();
    assert.equal(h.$(button.dataset.tab).hidden, false);
    assert.equal(button.attributes['aria-pressed'], 'true');
  }
  assert.equal(h.$('autoFilter').disabled, false);
  assert.equal(h.$('closeApp').disabled, false);
});

test('initialization keeps dependencies disabled, checks HTTP status/schema, and retries', async () => {
  const gate = deferred();
  const h = harness({'/api/bootstrap': {gate, ok: false}, '/catalog.json': {data: {maps: null}}});
  assert.equal(h.$('autoFilter').disabled, true);
  assert.equal(h.$('searchNasa').disabled, true);
  await h.$('researchFilter').dispatch('input');
  gate.resolve({token: 'must-not-accept-error-response'});
  await tick();
  assert.equal(h.$('closeApp').disabled, true);
  assert.equal(h.$('retryInit').hidden, false);
  await h.$('retryInit').click();
  assert.equal(h.$('retryInit').hidden, true);
  assert.equal(h.$('autoFilter').disabled, false);
  assert.equal(h.$('searchNasa').disabled, false);
});

test('NASA result/export are invalidated on input changes and a failed replacement', async () => {
  const h = await ready();
  h.$('nasaQuery').value = 'Moon';
  h.queue('/api/nasa', {data: research('Moon')});
  await h.$('searchNasa').click();
  assert.match(h.$('researchResults').textContent, /Search: “Moon”/);
  await h.$('exportResearch').click();
  assert.equal(JSON.parse(await h.downloads[0].blob.text()).query, 'Moon');
  h.$('nasaQuery').value = 'Mars';
  await h.$('nasaQuery').dispatch('input');
  assert.equal(h.$('exportResearch').disabled, true);
  assert.equal(h.$('researchResults').children.length, 0);
  h.queue('/api/nasa', {ok: false, data: {error: 'Publisher unavailable'}});
  await h.$('searchNasa').click();
  assert.equal(h.$('exportResearch').disabled, true);
  assert.match(h.$('status').textContent, /Publisher unavailable/);
});

test('older NASA response cannot replace a newer search even if transport ignores abort', async () => {
  const h = await ready();
  const old = deferred();
  h.$('nasaQuery').value = 'old';
  h.queue('/api/nasa', {gate: old, ignoreAbort: true});
  const oldAction = h.$('searchNasa').click();
  h.$('nasaQuery').value = 'new';
  await h.$('nasaQuery').dispatch('input');
  h.queue('/api/nasa', {data: research('new')});
  await h.$('searchNasa').click();
  old.resolve(research('old'));
  await oldAction;
  assert.match(h.$('researchResults').textContent, /Search: “new”/);
  assert.doesNotMatch(h.$('researchResults').textContent, /old/);
  assert.equal(h.$('exportResearch').disabled, false);
});

test('explicit cancel restores controls, explains scope and ignores late results', async () => {
  const h = await ready();
  const gate = deferred();
  h.$('nasaQuery').value = 'Moon';
  h.queue('/api/nasa', {gate, ignoreAbort: true});
  const pending = h.$('searchNasa').click();
  assert.equal(h.$('loadRegions').disabled, true);
  assert.equal(h.$('cancelOnline').hidden, false);
  await h.$('cancelOnline').click();
  assert.match(h.$('status').textContent, /server may still finish/);
  assert.equal(h.$('loadRegions').disabled, false);
  gate.resolve(research('late'));
  await pending;
  assert.equal(h.$('researchResults').children.length, 0);
  assert.equal(h.$('exportResearch').disabled, true);
});

test('a 30-second client deadline aborts the browser wait and allows retry', async () => {
  const h = await ready();
  h.$('nasaQuery').value = 'Moon';
  h.queue('/api/nasa', {gate: deferred()});
  const pending = h.$('searchNasa').click();
  h.runTimers(30000);
  await pending;
  assert.match(h.$('status').textContent, /30 seconds/);
  assert.equal(h.$('searchNasa').disabled, false);
  assert.equal(h.$('cancelOnline').hidden, true);
  assert.equal(h.$('exportResearch').disabled, true);
});

test('region change clears an old download and rejects a response for the prior selection', async () => {
  const h = await ready();
  await regions(h);
  await chooseRegion(h, 'a');
  h.queue('/api/map_freshness', {data: freshness('a')});
  await h.$('mapFreshness').click();
  assert.match(h.$('mapInfo').textContent, /Download Region A/);
  await chooseRegion(h, 'b');
  assert.equal(h.$('mapInfo').children.length, 0);
  const gate = deferred();
  h.queue('/api/map_freshness', {gate, ignoreAbort: true});
  const pending = h.$('mapFreshness').click();
  await chooseRegion(h, 'a');
  gate.resolve(freshness('b'));
  await pending;
  assert.equal(h.$('mapInfo').children.length, 0);
  h.queue('/api/map_freshness', {data: freshness('a')});
  await h.$('mapFreshness').click();
  await regions(h);
  assert.equal(h.$('mapInfo').children.length, 0);
  assert.equal(h.$('mapFreshness').disabled, true);
});

test('file replacement during reading prevents an obsolete check from being sent', async () => {
  const h = await ready();
  const textGate = deferred();
  await selectCode(h, {name: 'old.py', size: 1, text: () => textGate.promise});
  const old = h.$('reviewCode').click();
  await selectCode(h, codeFile('new.py'));
  h.queue('/api/review', {data: report('new.py', true)});
  await h.$('reviewCode').click();
  textGate.resolve('old source');
  await old;
  assert.equal(h.requests.filter(request => request.url === '/api/review').length, 1);
  assert.match(h.$('reviewResults').textContent, /new.py/);
  assert.match(h.$('reviewResults').textContent, /Checked 2026/);
  assert.match(h.$('reviewResults').textContent, /additional findings may be omitted/);
  await h.$('exportReview').click();
  assert.equal(JSON.parse(await h.downloads[0].blob.text()).file, 'new.py');
  await selectCode(h, codeFile('third.py'));
  assert.equal(h.$('exportReview').disabled, true);
  assert.equal(h.$('reviewResults').children.length, 0);
});

test('file replacement suppresses an old response already in flight', async () => {
  const h = await ready();
  const gate = deferred();
  await selectCode(h, codeFile('old.py'));
  h.queue('/api/review', {gate, ignoreAbort: true});
  const old = h.$('reviewCode').click();
  await tick();
  await selectCode(h, codeFile('new.py'));
  h.queue('/api/review', {data: report('new.py')});
  await h.$('reviewCode').click();
  gate.resolve(report('old.py'));
  await old;
  assert.match(h.$('reviewResults').textContent, /new.py/);
  assert.doesNotMatch(h.$('reviewResults').textContent, /old.py/);
});

test('close during image decode, lookup and review leaves controls closed and iframe unloaded', async () => {
  const h = await ready();
  const imageGate = deferred(), researchGate = deferred(), reviewGate = deferred();
  h.decode = () => imageGate.promise;
  const loading = selectImage(h);
  h.$('nasaQuery').value = 'Moon';
  h.queue('/api/nasa', {gate: researchGate, ignoreAbort: true});
  const searching = h.$('searchNasa').click();
  await selectCode(h, codeFile('late.py'));
  h.queue('/api/review', {gate: reviewGate, ignoreAbort: true});
  const reviewing = h.$('reviewCode').click();
  await tick();
  await h.$('loadMap').click();
  await h.$('closeApp').click();
  const decoded = bitmap();
  imageGate.resolve(decoded);
  researchGate.resolve(research('late'));
  reviewGate.resolve(report('late.py'));
  await Promise.all([loading, searching, reviewing]);
  assert.equal(h.$('savePng').disabled, true, 'late decode must not revive Save PNG');
  for (const element of h.document.querySelectorAll('button, input, select')) assert.equal(element.disabled, true, element.id);
  assert.equal(decoded.closed, true);
  assert.equal(h.$('mapFrame').src, undefined);
  assert.match(h.$('status').textContent, /^Workspace closed/);
  assert.equal(h.$('researchResults').children.length, 0);
  assert.equal(h.$('reviewResults').children.length, 0);
});

test('obsolete image failures do not overwrite newer state; transforms update dimensions', async () => {
  const h = await ready();
  const old = deferred();
  h.decode = file => file.name === 'old.png' ? old.promise : Promise.resolve(bitmap());
  const pending = selectImage(h, imageFile('old.png'));
  await selectImage(h, imageFile('new.png'));
  old.reject(Error('obsolete corrupt file'));
  await pending;
  assert.match(h.$('status').textContent, /new.png/);
  assert.doesNotMatch(h.$('status').textContent, /obsolete/);
  await h.$('rotateImage').click();
  assert.match(h.$('imageStatus').textContent, /80 × 120/);
  await h.$('cropImage').click();
  assert.match(h.$('imageStatus').textContent, /80 × 80/);
  await h.$('resetImage').click();
  assert.match(h.$('imageStatus').textContent, /120 × 80/);
  h.$('imageWidth').value = '60'; h.$('imageHeight').value = '40';
  await h.$('resizeImage').click();
  assert.match(h.$('imageStatus').textContent, /60 × 40/);
  await selectImage(h, {...imageFile('oversize.png'), size: 12 * 1024 * 1024 + 1});
  assert.match(h.$('status').textContent, /preview still shows new.png/);
  assert.equal(h.$('savePng').disabled, false);
  h.$('imageWidth').value = '8192'; h.$('imageHeight').value = '8192';
  await h.$('resizeImage').click();
  assert.match(h.$('status').textContent, /24 megapixels/);
  assert.equal(h.$('canvas').width, 60);
});

test('image export only saves the current preview; close suppresses a pending export', async () => {
  const h = await ready();
  await selectImage(h);
  let exportCallback;
  h.exportImage = ({callback}) => { exportCallback = callback; };
  const old = h.$('savePng').click();
  await h.$('rotateImage').click();
  exportCallback(new Blob(['old']));
  await old;
  assert.equal(h.downloads.length, 0);
  assert.match(h.$('status').textContent, /Image changed/);
  h.exportImage = null;
  await h.$('saveJpeg').click();
  assert.equal(h.downloads[0].name, 'prometheus-image.jpg');
  assert.equal(h.downloads[0].blob.type, 'image/jpeg');
  assert.ok(h.canvasCalls.some(call => call.operation === 'fillRect'));
  h.exportImage = ({callback}) => { exportCallback = callback; };
  const pending = h.$('savePng').click();
  await h.$('closeApp').click();
  exportCallback(new Blob(['late']));
  await pending;
  assert.equal(h.downloads.length, 1);
  assert.match(h.$('status').textContent, /^Workspace closed/);
});

test('map/place actions, filters, directory export, image filter inputs and PNG export remain wired', async () => {
  const h = await ready();
  h.$('latitude').value = '91';
  await h.$('loadMap').click();
  assert.match(h.$('status').textContent, /latitude/);
  h.$('latitude').value = '39';
  await h.$('loadMap').click();
  assert.match(h.$('mapFrame').src, /^https:\/\/www.openstreetmap.org\/export\/embed.html/);
  await h.$('unloadMap').click();
  assert.equal(h.$('mapFrame').hidden, true);
  h.$('placeQuery').value = 'New York';
  await h.$('placeSearch').click();
  assert.match(h.opened[0][0], /query=New\+York/);
  h.$('autoFilter').value = 'no_possible_match_fixture';
  await h.$('autoFilter').dispatch('input');
  assert.match(h.$('autoLinks').textContent, /No matches/);
  h.$('researchFilter').value = 'no_possible_match_fixture';
  await h.$('researchFilter').dispatch('input');
  assert.match(h.$('researchLinks').textContent, /No matches/);
  await h.$('exportAuto').click();
  assert.equal(JSON.parse(await h.downloads[0].blob.text()).automotive.length, realCatalog.automotive.length);
  await selectImage(h);
  for (const id of ['brightness', 'contrast', 'saturation']) { h.$(id).value = '80'; await h.$(id).dispatch('input'); }
  await h.$('savePng').click();
  assert.equal(h.downloads[1].name, 'prometheus-image.png');
});
