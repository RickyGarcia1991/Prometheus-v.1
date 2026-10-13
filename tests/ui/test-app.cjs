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

async function fixture({voice = null, autoVoicePrepare = true} = {}) {
  const dom = setup({runWorkspace: false}), {elements, context, documentRoot} = dom;
  const requests = [], timers = [], storageWrites = [], jobs = new Map(), queued = new Map();
  let serial = 0;
  const server = {token: 'first-token', statusFailure: false, pendingPost: null};
  const statusData = {version: '0.8.0.fixture', busy: false, job: null, state: 'idle', closing: false,
    hardware: {ram_gib: 8, available_ram_gib: 2, cpu_threads: 4, chat_ready: true}};
  context.document.hidden = false;
  context.document.querySelectorAll = selector => documentRoot.querySelectorAll(selector);
  const documentEvents = {};
  context.document.addEventListener = (name, callback) => { (documentEvents[name] ||= []).push(callback); };
  if (voice) context.window.PrometheusVoice = {LocalVoice: class {constructor() {return voice;}}};
  context.window.dispatchEvent = event => dom.emit(event.type, event.detail);
  context.CustomEvent = class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } };
  context.matchMedia = () => ({matches: false, addEventListener() {}});
  context.PrometheusAdaptive = {AdaptiveDisplay: class { update() { return {level: 0, name: 'Still', fps: 0, size: 0, reason: 'test'}; } }};
  context.Image = class { constructor() { this.complete = false; this.naturalWidth = 0; } };
  context.performance = {now: () => 1};
  context.localStorage = {getItem: () => null, setItem(key, value) {storageWrites.push({key, value});}, removeItem() {}};
  context.requestAnimationFrame = () => 1; context.cancelAnimationFrame = () => {};
  context.setTimeout = (fn, delay) => { timers.push({fn, delay}); return timers.length; };
  context.clearTimeout = id => { if(timers[id-1])timers[id-1].cancelled=true; };
  context.AbortSignal = {timeout: () => ({})};
  context.URL = URL;
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
      if(JSON.parse(options.body).mode==='dictate')jobs.set(id,{state:'running',action:'dictate',partial:'',stage:'Opening microphone',cancellable:true,recording:false});
      if(JSON.parse(options.body).mode==='voice_prepare')jobs.set(id,autoVoicePrepare?
        {state:'done',action:'voice_prepare',seconds:.1,result:{voice_ready:true,model:'fixture-model',context:2048,threads:2,batch:32}}:
        {state:'running',action:'voice_prepare',stage:'Preparing conversation · microphone off',cancellable:true});
      return response({id});
    }
    if (url.startsWith('/api/job/')) {
      const row = jobs.get(url.slice('/api/job/'.length));
      return row ? response(row) : response({error: 'Job not found'}, 404);
    }
    if (url === '/api/cancel') return response({cancel_requested: true});
    if (url === '/api/speech/cancel') return response({cancel_requested: true});
    if (url === '/api/speech') return response({encoding: 'wav-base64', audio: 'AAAA'});
    if (url === '/api/dictation/finish') return response({finish_requested: true});
    if (url === '/api/dictation/pulse') return response({recording: true});
    if (url === '/api/voice/pulse') return response({preparing: true});
    if (url === '/api/owner/lock') return response({locked: true});
    if (url === '/api/close') return response({closing: true, busy: false});
    throw new Error('Unexpected test request: ' + url);
  };
  const runtime = vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(asset, 'transport.js'), 'utf8'), runtime, {filename: 'transport.js'});
  vm.runInContext(fs.readFileSync(path.join(asset, 'folded-core.js'), 'utf8'), runtime, {filename: 'folded-core.js'});
  vm.runInContext(fs.readFileSync(path.join(asset, 'app.js'), 'utf8'), runtime, {filename: 'app.js'});
  await settle();
  const run = expression => vm.runInContext(expression, runtime);
  assert.equal(run('online'), true, 'fixture initialized');
  return {...dom, run, server, statusData, requests, timers, jobs, queued, storageWrites, documentEvents};
}

module.exports = {fixture, settle, deferred, response};
if (require.main === module) (async () => {
  const app = await fixture(), {run, elements: e, server, requests, jobs, queued, timers, context} = app;
  assert.equal(e.runtimeVersion.textContent, '0.8.0.fixture', 'footer uses the running server version');
  app.statusData.version = '0.9.0.updated';
  await run('status()');
  assert.equal(e.runtimeVersion.textContent, '0.9.0.updated', 'footer follows a new runtime version');
  delete app.statusData.version;
  await run('status()');
  assert.equal(e.runtimeVersion.textContent, 'Unavailable', 'missing version cannot retain a stale release label');
  const results = [];
  context.window.addEventListener('prometheus-result', event => results.push(event.detail));
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
  assert.equal(results.at(-1).origin, 'local', 'accepted actions belong to this browser tab');
  assert.equal(results.at(-1).job_id, 'held-job', 'result origin is bound to its accepted job');

  await run("submitAction({mode:'chat',prompt:'cancel this'})"); await settle(); const cancelId = run('job');
  assert.equal(e.cancelRequest.hidden, false); e.cancelRequest.dispatch('click'); await settle();
  const cancelRequest = requests.findLast(item => item.url === '/api/cancel'); assert.equal(cancelRequest.body.id, cancelId);
  assert.equal(e.cancelRequest.disabled, true);
  await run(`pollJob('${cancelId}')`); assert.equal(e.cancelRequest.disabled, true, 'polling cannot re-enable a pending Cancel');
  jobs.set(cancelId, {state: 'cancelled', seconds: 1, error: 'Stopped on request.', action: 'chat'});
  await run(`pollJob('${cancelId}')`); await settle();
  assert.equal(run('busy'), false); assert.equal(run('job'), null); assert(e.cancelRequest.hidden);
  assert(e.messages.children.some(item => item.querySelector('.label')?.textContent === 'Incomplete · not saved'));
  assert.equal(results.at(-1).origin, 'local', 'failed local actions keep their origin');
  assert.equal(results.at(-1).job_id, cancelId);

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
  // The server allows multiple local tabs. Observing another tab's work must
  // not replace this tab's editor draft or pretend the action originated here.
  const editor = await fixture(), ee = editor.elements, editorResults = [];
  editor.context.window.addEventListener('prometheus-result', event => editorResults.push(event.detail));
  editor.run(fs.readFileSync(path.join(asset, 'workspace.js'), 'utf8'));
  editor.run('setBusy(false)');
  async function finishEditor(action, details) {
    await settle(); const id = editor.run('job');
    editor.jobs.set(id, {state: 'done', seconds: 0, result: {action, details}});
    await editor.run(`pollJob('${id}')`); await settle();
  }
  ee.projectRoot.value = 'C:\\Projects\\demo'; ee.openProject.click();
  await finishEditor('code_open', {project: ee.projectRoot.value, files: [{path: 'app.py', size: 10}], test_presets: []});
  ee.codeFile.value = 'app.py'; ee.codeFile.dispatch('change'); ee.readCode.click();
  await finishEditor('code_read', {path: 'app.py', content: 'value = 1\n', sha256: 'original'});
  const draft = 'value = 999  # unsaved local draft\n';
  ee.codeEditor.value = draft; ee.codeEditor.dispatch('input');
  editor.statusData.busy = true; editor.statusData.job = 'other-tab-read';
  editor.jobs.set('other-tab-read', {state: 'running', cancellable: false});
  await editor.run('status()'); await settle();
  editor.jobs.set('other-tab-read', {state: 'done', seconds: 0, result: {action: 'code_read', details: {path: 'app.py', content: 'value = 2\n', sha256: 'other'}}});
  await editor.run("pollJob('other-tab-read')"); await settle();
  assert.equal(editorResults.at(-1).origin, 'observed', 'status-adopted work is explicitly observed');
  assert.equal(editorResults.at(-1).job_id, 'other-tab-read');
  assert.equal(ee.codeEditor.value, draft, 'another tab reading a file cannot discard this tab\'s draft');
  // A status poll between server acceptance and the POST response must leave
  // result ownership to that response instead of adopting the pending job.
  const accepting = await fixture(), hold = deferred();
  accepting.server.pendingPost = hold;
  const acceptingPost = accepting.run("submitAction({mode:'code_read',project:'C:\\\\Projects\\\\demo',file:'app.py'})");
  await settle(); accepting.statusData.busy = true; accepting.statusData.job = 'accepted-late';
  accepting.jobs.set('accepted-late', {state: 'running', cancellable: false});
  await accepting.run('status()'); await settle();
  assert.equal(accepting.run('job'), null, 'status does not adopt a job while its local submission is pending');
  hold.resolve(response({id: 'accepted-late'})); assert.equal(await acceptingPost, true); await settle();
  const acceptingResults = [];
  accepting.context.window.addEventListener('prometheus-result', event => acceptingResults.push(event.detail));
  accepting.jobs.set('accepted-late', {state: 'done', seconds: 0, result: {action: 'code_read', details: {}}});
  await accepting.run("pollJob('accepted-late')"); await settle();
  assert.equal(acceptingResults.at(-1).origin, 'local');
  assert.equal(acceptingResults.at(-1).job_id, 'accepted-late');
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
  const grounded = await fixture(), ge = grounded.elements;
  ge.mode.value = 'ask'; ge.evidenceSource.value = 'project'; ge.project.value = 'C:\\Projects\\demo';
  grounded.run('updateFields()');
  assert.equal(ge.sourceField.hidden, false); assert.equal(ge.projectField.hidden, false);
  assert.equal(ge.profileField.hidden, true); assert.equal(ge.imageField.hidden, true);
  ge.prompt.value = 'Where is the timeout configured?';
  await grounded.run('submit()'); await settle();
  assert.equal(grounded.requests.findLast(r => r.url === '/api/jobs').body.source, 'project');
  grounded.run('transport.onRestart(); setBusy(false)');
  ge.evidenceSource.value = 'offline'; grounded.run('updateFields()');
  assert.equal(ge.profileField.hidden, false); assert.equal(ge.projectField.hidden, true);
  ge.mode.value = 'chat'; grounded.run('updateFields()'); ge.prompt.value = 'Hello';
  await grounded.run('submit()'); await settle();
  assert.equal(grounded.requests.findLast(r => r.url === '/api/jobs').body.source, undefined);
  grounded.run('transport.onRestart(); setBusy(false)'); ge.prompt.value = '';
  await ge.capabilities.onclick(); await settle();
  assert.equal(grounded.requests.findLast(r => r.url === '/api/jobs').body.mode, 'capabilities');
  grounded.run('transport.onRestart(); setBusy(false)');
  ge.mode.value = 'vision'; ge.prompt.value = 'Describe this image'; grounded.run('updateFields()');
  assert.equal(ge.imageField.hidden, false);
  const beforeImage = grounded.requests.filter(r => r.url === '/api/jobs').length;
  ge.questionImage.files = [{size: 8*1024*1024+1}];
  await grounded.run('submit()'); await settle();
  assert.equal(grounded.requests.filter(r => r.url === '/api/jobs').length, beforeImage);
  assert(ge.notice.textContent.includes('8 MiB'));
  grounded.context.FileReader = class {readAsDataURL() {this.result = 'data:image/png;base64,aGVhZGVy'; this.onload();}};
  ge.questionImage.files = [{size: 6}]; ge.questionImage.value = 'image.png';
  await grounded.run('submit()'); await settle();
  assert.equal(grounded.requests.findLast(r => r.url === '/api/jobs').body.content, 'aGVhZGVy');
  assert.equal(ge.questionImage.value, '');
  assert(!JSON.stringify(grounded.storageWrites).includes('aGVhZGVy'), 'image is never saved in browser storage');
  console.log('Evidence and image UI passed: explicit source, bounded image selection, image clearing, capability discovery.');
  console.log('App lifecycle tests passed: submission/result races, cancel, disconnect, 404, stale polls and 403 token restart.');
  console.log('Smart home UI passed: masked key, Home-only submission, dictation review, no localStorage, close/restart clearing.');
  const links = await fixture();
  const sourceUrl = 'https://github.com/FrameworkComputer/Framework-Laptop-12/blob/pinned/Mainboard/README.md?plain=1#rtc';
  const footer = 'Documented answer [S1].\n\nSources supplied:\n[S1] Mainboard reference — '+sourceUrl+' | text, characters 5610-6829';
  links.context.sourceFooter = footer;
  links.run("append('assistant', sourceFooter)");
  let shown = links.elements.messages.children.at(-1).querySelector('.text');
  let anchors = shown.querySelectorAll('.source-link');
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].textContent, 'Mainboard reference', 'short document label replaces the long URL');
  assert.equal(anchors[0].href, sourceUrl, 'full pinned URL, query and fragment are preserved');
  assert.equal(anchors[0].title, sourceUrl);
  assert.equal(anchors[0].target, '_blank');
  assert.equal(anchors[0].rel, 'noopener noreferrer');
  const visible = shown.children.map(n=>n.textContent).join('');
  assert(visible.includes('[S1] Mainboard reference | text, characters 5610-6829'));
  assert(!visible.includes('https://'), 'source footer contains no expanded URL');
  links.elements.history.add(new links.context.Option('Saved evidence', 'saved-evidence'));
  links.elements.history.value = 'saved-evidence';
  links.queued.set('/api/history/saved-evidence', [Promise.resolve(response([{role:'assistant',content:footer}]))]);
  await links.elements.history.onchange();
  assert.equal(links.elements.messages.children[0].querySelectorAll('.source-link')[0].href, sourceUrl, 'saved conversations get the same clickable source');
  const savedDetails = {model: 'saved-model', generated_answer: true, grounding: {
    sources: [{id: 'S1', title: 'Mainboard reference', location: sourceUrl,
               excerpt: 'The exact saved excerpt <script>must remain literal</script>.', sha256: 'a'.repeat(64)}],
    recognized_citations: ['S1'], unrecognized_citations: [], claim_verification: 'not performed'}};
  links.elements.workLabel.textContent = 'Unrelated previous model';
  links.elements.requestTime.textContent = '999 s';
  links.queued.set('/api/history/saved-evidence', [Promise.resolve(response([
    {role: 'user', content: 'What does the source say?'},
    {role: 'assistant', content: footer, label: 'Saved evidence model', details: savedDetails}]))]);
  await links.elements.history.onchange();
  const restoredReply = links.elements.messages.children.at(-1);
  assert.equal(restoredReply.querySelector('.label').textContent, 'Saved evidence model');
  const restoredPanel = restoredReply.children.find(node => node.tagName === 'DETAILS');
  assert.equal(restoredPanel.children.find(node => node.tagName === 'SUMMARY').textContent, 'Sources and result details');
  const restoredMetadata = restoredPanel.children.find(node => node.tagName === 'PRE');
  assert.deepEqual(JSON.parse(restoredMetadata.textContent), savedDetails);
  assert.equal(restoredMetadata.children.length, 0, 'metadata is literal text, never HTML');
  assert.equal(restoredReply.querySelectorAll('.source-link')[0].href, sourceUrl);
  assert.equal(links.elements.workLabel.textContent, 'Saved evidence model');
  assert.equal(links.elements.requestTime.textContent, '', 'prior request time is cleared on history navigation');
  assert.equal(links.elements.saveStatus.textContent, 'Saved locally');
  links.queued.set('/api/history/saved-evidence', [Promise.resolve(response([{role: 'assistant', content: footer}]))]);
  await links.elements.history.onchange();
  assert.equal(links.elements.workLabel.textContent, 'Saved conversation', 'legacy history never inherits another model label');
  assert(!links.elements.messages.children.at(-1).children.some(node => node.tagName === 'DETAILS'), 'legacy sources are not fabricated');
  links.elements.requestTime.textContent = '42 s';
  links.elements.newChat.onclick();
  assert.equal(links.elements.messages.children.length, 0);
  assert.equal(links.elements.workLabel.textContent, 'Ready for your next idea');
  assert.equal(links.elements.requestTime.textContent, '');
  assert.equal(links.elements.saveStatus.textContent, 'Ready · nothing sent');
  for (const unsafe of ['javascript:alert(1)', 'data:text/html,hi', 'file:///C:/secret', 'https://user:password@example.com/private', 'https://']) {
    links.context.badFooter = '[S2] <img src=x onerror=alert(1)> — '+unsafe;
    links.run("append('assistant',badFooter)");
    const box = links.elements.messages.children.at(-1).querySelector('.text');
    assert.equal(box.querySelectorAll('.source-link').length,0, 'unsafe or malformed targets remain plain text');
    assert.equal(box.textContent,links.context.badFooter);
  }
  links.run("append('user',sourceFooter)");
  assert.equal(links.elements.messages.children.at(-1).querySelectorAll('.source-link').length,0,'user input remains literal');
  links.context.bareSource='https://example.com/manual.pdf | page 12';
  links.run("append('assistant',bareSource)");
  assert.equal(links.elements.messages.children.at(-1).querySelectorAll('.source-link')[0].textContent,'example.com','standalone excerpt source URLs use a compact domain label');
  console.log('Source links passed: live/saved replies, exact destination, compact label, safe new tab, literal unsafe input.');
  console.log('Saved evidence passed: exact metadata, labels, literal rendering, legacy history, and conversation status reset.');
  const reach = await fixture();
  reach.elements.mode.value = 'reach'; reach.elements.reachAction.value = 'github-releases';
  reach.elements.mode.onchange();
  assert.equal(reach.elements.reachField.hidden, false);
  assert.equal(reach.elements.prompt.placeholder, 'Panniantong/Agent-Reach');
  reach.elements.prompt.value = 'Panniantong/Agent-Reach';
  await reach.run('submit()'); await settle();
  assert.equal(reach.requests.findLast(r=>r.url==='/api/jobs').body.reach_action, 'github-releases');
  assert.equal(reach.requests.findLast(r=>r.url==='/api/jobs').body.mode, 'reach');
  const webSearch = await fixture();
  webSearch.elements.mode.value = 'reach'; webSearch.elements.reachAction.value = 'web-search';
  webSearch.elements.mode.onchange();
  assert.equal(webSearch.elements.prompt.placeholder, 'Search the web for a topic…');
  webSearch.elements.prompt.value = 'portable document readers';
  await webSearch.run('submit()'); await settle();
  const searchBody = webSearch.requests.findLast(r=>r.url==='/api/jobs').body;
  assert.equal(searchBody.reach_action, 'web-search');
  assert.equal(searchBody.prompt, 'portable document readers');
  for (const query of ['', 'coding assistant']) {
    const catalogue = await fixture();
    catalogue.elements.mode.value = 'reach'; catalogue.elements.reachAction.value = 'agent-resources';
    catalogue.elements.reachAction.onchange();
    assert.equal(catalogue.elements.prompt.placeholder, 'Project, capability, or all');
    assert.equal(catalogue.elements.prompt.required, false, 'native form validation permits browsing without a query');
    assert.equal(catalogue.elements.prompt.maxLength, 240);
    assert.match(catalogue.elements.reachHelp.textContent, /local.*catalogue.*No web access is required/);
    catalogue.elements.prompt.value = query;
    await catalogue.run('submit()'); await settle();
    const body = catalogue.requests.findLast(r=>r.url==='/api/jobs').body;
    assert.equal(body.reach_action, 'agent-resources');
    assert.equal(body.prompt, query || 'all', 'only an empty catalogue query becomes all');
  }
  const emptyWeb = await fixture();
  emptyWeb.elements.mode.value = 'reach'; emptyWeb.elements.reachAction.value = 'agent-resources'; emptyWeb.elements.mode.onchange();
  emptyWeb.elements.reachAction.value = 'web-search'; emptyWeb.elements.reachAction.onchange();
  assert.equal(emptyWeb.elements.prompt.required, true); assert.equal(emptyWeb.elements.prompt.maxLength, 4000);
  assert.match(emptyWeb.elements.reachHelp.textContent, /sends your query to Firecrawl/);
  await emptyWeb.run('submit()');
  assert(!emptyWeb.requests.some(r=>r.url==='/api/jobs'), 'empty online search never becomes an all query');
  console.log('Public reach UI passed: explicit online mode, Firecrawl search, operation guidance and exact job fields.');
})().catch(error => { console.error(error); process.exitCode = 1; });
