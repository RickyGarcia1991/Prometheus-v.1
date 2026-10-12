'use strict';
// Synthetic browser contracts only: no authenticator, credentials, or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const asset = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
const markup = fs.readFileSync(path.join(asset, 'owner-access.html'), 'utf8');
const script = fs.readFileSync(path.join(asset, 'owner-access.js'), 'utf8');
async function settle() { for (let i = 0; i < 60; i++) await Promise.resolve(); }
async function fixture(state = {}, {ticket = '', supported = true} = {}) {
  const nodes = {};
  for (const match of markup.matchAll(/<(?:button|a|h1|p)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    nodes[match[2]] = {hidden: /\bhidden\b/.test(match[1]), disabled: /\bdisabled\b/.test(match[1]),
      textContent: '', listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; }};
  }
  const current = {required: true, ready: true, enrolled: false, unlocked: false, ...state};
  const requests = [], credentialCalls = [], redirects = [], historyWrites = [], timers = [];
  const server = {state: current, fail: null, credentialError: null, hold: null};
  const bytes = () => Uint8Array.of(1, 2, 3).buffer;
  const credentials = {
    async create(options) { credentialCalls.push({kind: 'register', options}); if (server.hold) await server.hold;
      if (server.credentialError) throw server.credentialError;
      return {id: 'synthetic', rawId: bytes(), type: 'public-key', response: {clientDataJSON: bytes(), attestationObject: bytes()}, getClientExtensionResults: () => ({})}; },
    async get(options) { credentialCalls.push({kind: 'authenticate', options});
      if (server.credentialError) throw server.credentialError;
      return {id: 'synthetic', rawId: bytes(), type: 'public-key', response: {clientDataJSON: bytes(), authenticatorData: bytes(), signature: bytes()}, getClientExtensionResults: () => ({})}; }
  };
  let kind;
  const context = {document: {getElementById: id => nodes[id]},
    location: {hash: ticket ? '#setup=' + ticket : '', pathname: '/owner-access', replace: value => redirects.push(value)},
    history: {replaceState: (...args) => historyWrites.push(args)}, URLSearchParams, Uint8Array, atob, btoa,
    window: supported ? {PublicKeyCredential: function () {}} : {}, navigator: {credentials},
    setInterval: callback => timers.push(callback),
    fetch: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({url, body, token: options.headers?.['X-Prometheus-Token']});
      if (server.fail === url) return {ok: false, status: 403, json: async () => ({error: 'Synthetic request failure.'})};
      let result;
      if (url === '/api/bootstrap') result = {token: 'synthetic-token', owner_access: {...server.state}};
      else if (url === '/api/owner/state') result = {...server.state};
      else if (url === '/api/owner/begin') {
        kind = body.kind;
        result = {request_id: 'synthetic-request', options: {publicKey: {challenge: 'AQID',
          ...(kind === 'register' ? {user: {id: 'AQID'}, excludeCredentials: [{id: 'AQID'}]} : {allowCredentials: [{id: 'AQID'}]})}}};
      } else if (url === '/api/owner/complete') {
        if (kind === 'register') { server.state.enrolled = true; result = {registered: true, unlocked: false}; }
        else { server.state.unlocked = true; result = {unlocked: true}; }
      } else if (url === '/api/owner/lock') { server.state.unlocked = false; result = {locked: true}; }
      else throw new Error('Unexpected synthetic request: ' + url);
      return {ok: true, json: async () => result};
    }};
  vm.runInNewContext(script, context, {filename: 'owner-access.js'});
  await settle();
  async function click(id) { assert(nodes[id], 'Missing control ' + id); assert(!nodes[id].hidden && !nodes[id].disabled, id + ' must be usable'); nodes[id].listeners.click(); await settle(); }
  return {nodes, requests, credentialCalls, redirects, historyWrites, timers, server, click};
}

test('unenrolled workspace requires the private launcher and never invokes credentials on load', async () => {
  const h = await fixture();
  assert(h.nodes.ownerUnlock.disabled); assert(h.nodes.ownerEnroll.hidden);
  assert.equal(h.credentialCalls.length, 0); assert.equal(h.requests.filter(r => r.body).length, 0);
});

test('cancelled first enrollment keeps Unlock disabled and preserves an explicit retry', async () => {
  const h = await fixture({}, {ticket: 'synthetic-test-ticket'});
  h.server.credentialError = Object.assign(new Error('Cancelled'), {name: 'NotAllowedError'});
  await h.click('ownerEnroll');
  assert(h.nodes.ownerUnlock.disabled); assert(!h.nodes.ownerEnroll.hidden && !h.nodes.ownerEnroll.disabled);
  assert.equal(h.requests.filter(r => r.url === '/api/owner/complete').length, 0);
  assert.equal(h.historyWrites[0][2], '/owner-access');
  assert(!Object.values(h.nodes).some(n => n.textContent.includes('synthetic-test-ticket')));
});

test('first registration permits a separate user-triggered signed unlock', async () => {
  const h = await fixture({}, {ticket: 'synthetic-test-ticket'});
  await h.click('ownerEnroll');
  assert(!h.nodes.ownerUnlock.hidden && !h.nodes.ownerUnlock.disabled); assert(h.nodes.ownerEnroll.hidden);
  assert.equal(h.redirects.length, 0); assert.equal(h.credentialCalls.length, 1);
  assert(h.credentialCalls[0].options.publicKey.challenge instanceof Uint8Array);
  await h.click('ownerUnlock'); assert.deepEqual(h.redirects, ['/']);
  assert.equal(h.requests.findLast(r => r.url === '/api/owner/begin').body.setup_ticket, undefined);
});

test('backup registration offers return to chat and explicit lock-and-test without automatic authentication', async () => {
  const h = await fixture({enrolled: true, unlocked: true});
  await h.click('ownerEnroll');
  assert(!h.nodes.ownerReturn.hidden); assert(markup.includes('id="ownerReturn" href="/"'));
  assert(!h.nodes.ownerVerify.hidden && !h.nodes.ownerVerify.disabled);
  assert.equal(h.credentialCalls.length, 1); assert.equal(h.requests.filter(r => r.url === '/api/owner/lock').length, 0);
  await h.click('ownerVerify');
  assert(!h.nodes.ownerUnlock.hidden && !h.nodes.ownerUnlock.disabled); assert(h.nodes.ownerReturn.hidden);
  assert.equal(h.credentialCalls.length, 1, 'locking must not automatically open a credential prompt');
  await h.click('ownerUnlock'); assert.equal(h.credentialCalls.at(-1).kind, 'authenticate');
});

test('registered locked state cannot add credentials and authentication cancellation remains retryable', async () => {
  const h = await fixture({enrolled: true});
  assert(h.nodes.ownerEnroll.hidden);
  h.server.credentialError = Object.assign(new Error('Cancelled'), {name: 'NotAllowedError'});
  await h.click('ownerUnlock'); assert(!h.nodes.ownerUnlock.disabled); assert.equal(h.redirects.length, 0);
});

test('missing components or browser support fail closed', async () => {
  for (const [state, options] of [[{enrolled: true, ready: false}, {}], [{enrolled: true}, {supported: false}]]) {
    const h = await fixture(state, options);
    assert(h.nodes.ownerUnlock.disabled); assert(h.nodes.ownerEnroll.hidden || h.nodes.ownerEnroll.disabled);
    assert.equal(h.credentialCalls.length, 0); assert.equal(h.redirects.length, 0);
  }
});

test('failed registration refreshes expired state and does not replay the operation', async () => {
  const h = await fixture({enrolled: true, unlocked: true});
  h.server.state.unlocked = false; h.server.fail = '/api/owner/begin';
  await h.click('ownerEnroll');
  assert(h.nodes.ownerEnroll.hidden); assert(!h.nodes.ownerUnlock.disabled);
  assert.equal(h.requests.filter(r => r.url === '/api/owner/begin').length, 1);
  assert.equal(h.credentialCalls.length, 0);
});

test('periodic state refresh notices session expiry without starting sign-in', async () => {
  const h = await fixture({enrolled: true, unlocked: true});
  h.server.state.unlocked = false; h.timers[0](); await settle();
  assert(h.nodes.ownerEnroll.hidden); assert(!h.nodes.ownerUnlock.hidden && !h.nodes.ownerUnlock.disabled);
  assert.equal(h.credentialCalls.length, 0);
});

test('active credential operation blocks duplicate clicks', async () => {
  const h = await fixture({}, {ticket: 'synthetic-test-ticket'});
  let release; h.server.hold = new Promise(resolve => { release = resolve; });
  await h.click('ownerEnroll'); assert(h.nodes.ownerEnroll.disabled && h.nodes.ownerUnlock.disabled);
  h.nodes.ownerEnroll.listeners.click(); await settle();
  assert.equal(h.requests.filter(r => r.url === '/api/owner/begin').length, 1);
  release(); await settle(); assert(!h.nodes.ownerUnlock.disabled);
});
