'use strict';
// Exercise app + transport lifecycle with deterministic requests, no browser/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {setup} = require('./test-workspace.cjs');
const asset = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }
function response(data, status = 200) { return {ok: status >= 200 && status < 300, status, json: async () => data}; }
async function settle() { for (let index = 0; index < 40; index++) await Promise.resolve(); }

async function fixture() {
  const dom = setup({runWorkspace: false}), {elements, context, documentRoot} = dom;
  const requests = [], timers = [], storageWrites = [], jobs = new Map(), queued = new Map();
  let serial = 0;
  const server = {token: 'first-token', statusFailure: false, pendingPost: null};
  const statusData = {busy: false, job: null, state: 'idle', closing: false,
    hardware: {ram_gib: 8, available_ram_gib: 2, cpu_threads: 4, chat_ready: true}};
  context.document.hidden = false;
  context.document.querySelectorAll = selector => documentRoot.querySelectorAll(selector);
  context.document.addEventListener = () => {};
  context.window.dispatchEvent = event => dom.emit(event.type, event.detail);
  context.CustomEvent = class { constructor(type, options) { this.type = type; this.detail = options.detail; } };
  context.matchMedia = () => ({matches: false, addEventListener() {}});
  context.PrometheusAdaptive = {AdaptiveDisplay: class { update() { return {level: 0, name: 'Still', fps: 0, size: 0, reason: 'test'}; } }};
  context.Image = class { constructor() { this.complete = false; this.naturalWidth = 0; } };
  context.performance = {now: () => 1};
  context.localStorage = {getItem: () => null, setItem(key, value) {storageWrites.push({key, value});}, removeItem() {}};
  context.requestAnimationFrame = () => 1; context.cancelAnimationFrame = () => {};
  context.setTimeout = (fn, delay) => { timers.push({fn, delay}); return timers.length; };
  context.AbortSignal = {timeout: () => ({})};
  context.fetch = async (url, options = {}) => {
    requests.push({url, options, body: options.body ? JSON.parse(options.body) : null});
    const queue = queued.get(url);
    if (queue?.length) return await queue.shift();
    if (url === '/api/bootstrap') return response({token: server.token, languages: ['Auto'], providers: [{id: 'crossref', title: 'Crossref'}]});
    if (url === '/api/status') {
      if (server.statusFailure) throw new Error('connection lost');
      return response(statusData);
    }
    if (url === '/api/sessions') return response([]);
    if (url === '/api/jobs') {
      if (server.pendingPost) { const pending = server.pendingPost; server.pendingPost = null; return await pending.promise; }
      const id = 'job-' + (++serial); jobs.set(id, {state: 'running', partial: 'working text', stage: 'Generating', cancellable: true});
      return response({id});
    }
    if (url.startsWith('/api/job/')) {
      const row = jobs.get(url.slice('/api/job/'.length));
      return row ? response(row) : response({error: 'Job not found'}, 404);
    }
    if (url === '/api/cancel') return response({cancel_requested: true});
    if (url === '/api/close') return response({closing: true, busy: false});
    throw new Error('Unexpected test request: ' + url);
  };
  const runtime = vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(asset, 'transport.js'), 'utf8'), runtime, {filename: 'transport.js'});
  vm.runInContext(fs.readFileSync(path.join(asset, 'app.js'), 'utf8'), runtime, {filename: 'app.js'});
  await settle();
  const run = expression => vm.runInContext(expression, runtime);
  assert.equal(run('online'), true, 'fixture initialized');
  return {...dom, run, server, statusData, requests, timers, jobs, queued, storageWrites};
}

(async () => {
  const app = await fixture(), {run, elements: e, server, requests, jobs, queued, timers, context} = app;
  const postCount = () => requests.filter(item => item.url === '/api/jobs').length;
  const heldPost = deferred(); server.pendingPost = heldPost;
  const firstSubmit = run("submitAction({mode:'chat',prompt:'first'})"); await settle();
  assert.equal(run('submitting'), true); assert.equal(run('busy'), true);
  await run('status()'); assert.equal(run('busy'), true, 'idle status cannot unlock an unaccepted submission');
  assert.equal(await run("submitAction({mode:'chat',prompt:'duplicate'})"), false);
  assert.equal(postCount(), 1, 'only one POST while first acceptance is pending');
  jobs.set('held-job', {state: 'running', partial: 'partial answer', cancellable: true});
  heldPost.resolve(response({id: 'held-job'})); assert.equal(await firstSubmit, true); await settle();
  await run('status()'); assert.equal(run('busy'), true, 'idle status cannot unlock an unrendered result');
  let nestedSubmission = null;
  context.window.addEventListener('prometheus-result', event => {
    if (event.detail.label === 'Final one') nestedSubmission = run("submitAction({mode:'chat',prompt:'too early'})");
  });
  jobs.set('held-job', {state: 'done', seconds: 1, result: {reply: 'final answer', label: 'Final one', action: 'chat'}});
  await run("pollJob('held-job')"); await settle();
  assert.equal(await nestedSubmission, false, 'second submit denied until final result handlers finish');
  assert.equal(run('busy'), false); assert.equal(run('job'), null);
  assert(e.messages.children.some(item => item.querySelector('.text')?.textContent === 'final answer'));

  await run("submitAction({mode:'chat',prompt:'cancel this'})"); await settle(); const cancelId = run('job');
  assert.equal(e.cancelRequest.hidden, false); e.cancelRequest.dispatch('click'); await settle();
  const cancelRequest = requests.findLast(item => item.url === '/api/cancel'); assert.equal(cancelRequest.body.id, cancelId);
  assert.equal(e.cancelRequest.disabled, true);
  await run(`pollJob('${cancelId}')`); assert.equal(e.cancelRequest.disabled, true, 'polling cannot re-enable a pending Cancel');
  jobs.set(cancelId, {state: 'cancelled', seconds: 1, error: 'Stopped on request.', action: 'chat'});
  await run(`pollJob('${cancelId}')`); await settle();
  assert.equal(run('busy'), false); assert.equal(run('job'), null); assert(e.cancelRequest.hidden);
  assert(e.messages.children.some(item => item.querySelector('.label')?.textContent === 'Incomplete · not saved'));

  await run("submitAction({mode:'chat',prompt:'temporary disconnect'})"); await settle(); const lostId = run('job');
  queued.set('/api/job/' + lostId, [Promise.reject(new Error('temporary network loss'))]);
  await run(`pollJob('${lostId}')`);
  assert.equal(run('job'), lostId); assert.equal(run('busy'), true);
  assert(timers.some(item => item.delay === 5000), 'disconnected job schedules a bounded retry');
  assert(e.notice.textContent.includes('reconnecting'));
  server.statusFailure = true; await run('status()'); assert.equal(run('online'), false); assert.equal(e.send.disabled, true);
  server.statusFailure = false; await run('status()'); assert.equal(run('online'), true); assert.equal(run('busy'), true);
  jobs.delete(lostId); await run(`pollJob('${lostId}')`); await settle();
  assert.equal(run('job'), null); assert.equal(run('busy'), false); assert(e.notice.textContent.includes('no longer available'));

  await run("submitAction({mode:'chat',prompt:'stale job'})"); await settle(); const oldId = run('job');
  const oldResponse = deferred(); queued.set('/api/job/' + oldId, [oldResponse.promise]);
  const oldPoll = run(`pollJob('${oldId}')`); await settle();
  // A server restart makes the old job unavailable; a fresh explicit request follows.
  run('transport.onRestart(); setBusy(false)');
  await run("submitAction({mode:'chat',prompt:'fresh request'})"); await settle(); const newId = run('job');
  assert.notEqual(newId, oldId);
  oldResponse.resolve(response({state: 'done', seconds: 9, result: {reply: 'stale answer', label: 'Old'}}));
  await oldPoll; assert.equal(run('job'), newId); assert.equal(run('busy'), true, 'late old poll cannot clear new job');
  assert(!e.messages.children.some(item => item.querySelector('.text')?.textContent === 'stale answer'));
  jobs.delete(newId); await run(`pollJob('${newId}')`); await settle();

  // Real LocalClient branch: 403 refreshes a changed token and calls restart recovery.
  await run("submitAction({mode:'chat',prompt:'before token change'})"); await settle(); const restartId = run('job');
  run("lastSubmitted='recover this user text'"); e.prompt.value = '';
  const beforeRestartPosts = postCount(); server.token = 'replacement-token';
  queued.set('/api/job/' + restartId, [Promise.resolve(response({error: 'Old session'}, 403))]);
  jobs.delete(restartId);
  await run(`pollJob('${restartId}')`); await settle();
  assert.equal(run('job'), null); assert.equal(e.prompt.value, 'recover this user text');
  assert.equal(postCount(), beforeRestartPosts, 'restart does not resubmit an unfinished job');
  const retried = requests.filter(item => item.url === '/api/job/' + restartId).at(-1);
  assert.equal(retried.options.headers['X-Prometheus-Token'], 'replacement-token');
  await run('status()'); assert.equal(run('busy'), false, 'next status makes restarted interface usable');
  assert.equal(e.send.disabled, false);
  const home = await fixture(), he = home.elements;
  const secret = 'fixture-control-key-123456789';
  he.mode.value = 'home'; home.run('updateFields()');
  assert.equal(he.homeKeyField.hidden, false);
  assert.equal(he.homeKey.getAttribute('type'), 'password');
  assert.equal(he.homeKey.getAttribute('autocomplete'), 'off');
  he.homeKey.value = secret; he.prompt.value = 'status front porch lights';
  await home.run('submit()'); await settle();
  let homePost = home.requests.findLast(item => item.url === '/api/jobs');
  assert.equal(homePost.body.mode, 'home'); assert.equal(homePost.body.home_key, secret);
  let homeJob = home.run('job');
  home.jobs.set(homeJob, {state:'done', seconds:0, result:{reply:'Front porch lights: off', label:'Smart home'}});
  await home.run(`pollJob('${homeJob}')`); await settle();
  await home.run("submit('dictate')"); await settle();
  assert.equal(home.requests.findLast(item => item.url === '/api/jobs').body.home_key, undefined);
  homeJob = home.run('job');
  home.jobs.set(homeJob, {state:'done', seconds:0, result:{transcript:'turn off bedroom lights'}});
  await home.run(`pollJob('${homeJob}')`); await settle();
  assert.equal(he.mode.value, 'home'); assert.equal(he.prompt.value, 'turn off bedroom lights');
  he.mode.value = 'chat'; home.run('updateFields()'); assert(he.homeKeyField.hidden);
  he.prompt.value = 'Hello'; await home.run('submit()'); await settle();
  assert.equal(home.requests.findLast(item => item.url === '/api/jobs').body.home_key, undefined);
  assert(!JSON.stringify(home.storageWrites).includes(secret), 'control key never enters localStorage');
  home.run('transport.onRestart()'); assert.equal(he.homeKey.value, '');
  he.homeKey.value = secret; await he.close.onclick(); assert.equal(he.homeKey.value, '');
  console.log('App lifecycle tests passed: submission/result races, cancel, disconnect, 404, stale polls and 403 token restart.');
  console.log('Smart home UI passed: masked key, Home-only submission, dictation review, no localStorage, close/restart clearing.');
})().catch(error => { console.error(error); process.exitCode = 1; });
