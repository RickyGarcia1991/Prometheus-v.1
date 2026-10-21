'use strict';
// Pure transport tests. No browser, DOM, network listener, or model is used.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { LocalClient } = require('./../../src/prometheus_assistant/ui_assets/transport.js');

function response(status, data) {
  return { status, ok: status >= 200 && status < 300, json: async () => data };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('fetch uses the global receiver required by browser Window.fetch', async () => {
  const client = new LocalClient(async function (path) {
    assert.equal(this, globalThis, 'native fetch must not receive the LocalClient as its receiver');
    return response(200, path === '/api/bootstrap' ? {token: 'bound-token'} : {ready: true});
  });
  assert.deepEqual(await client.request('/api/status'), {ready: true});
});

test('concurrent bootstrap calls share one fetch and one token', async () => {
  const pending = deferred();
  const calls = [];
  const client = new LocalClient(async (path, options) => {
    calls.push({ path, options });
    return pending.promise;
  });
  const first = client.bootstrap();
  const second = client.bootstrap();
  const third = client.request('/api/bootstrap');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/api/bootstrap');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.ok(calls[0].options.signal instanceof AbortSignal);
  pending.resolve(response(200, { token: 'session-one', languages: ['Python'] }));
  const results = await Promise.all([first, second, third]);
  assert.deepEqual(results, Array(3).fill({ token: 'session-one', languages: ['Python'] }));
  assert.equal(client.token, 'session-one');
  assert.equal(client.bootstrapPromise, null);
});

test('concurrent first requests bootstrap once and then send the acquired token', async () => {
  const pending = deferred();
  const calls = [];
  const client = new LocalClient(async (path, options) => {
    calls.push({ path, options });
    return path === '/api/bootstrap' ? pending.promise : response(200, { path });
  });
  const requests = [client.request('/api/status'), client.request('/api/sessions')];
  assert.equal(calls.length, 1);
  pending.resolve(response(200, { token: 'new-token' }));
  assert.deepEqual(await Promise.all(requests), [{ path: '/api/status' }, { path: '/api/sessions' }]);
  assert.equal(calls.filter(call => call.path === '/api/bootstrap').length, 1);
  for (const call of calls.slice(1)) assert.equal(call.options.headers['X-Prometheus-Token'], 'new-token');
});

test('403 renews the token and retries the rejected action exactly once', async () => {
  const calls = [];
  const client = new LocalClient(async (path, options) => {
    calls.push({ path, options });
    if (path === '/api/bootstrap') return response(200, { token: 'replacement' });
    return options.headers['X-Prometheus-Token'] === 'expired'
      ? response(403, { error: 'Expired session' }) : response(200, { id: 'job-one' });
  });
  client.token = 'expired';
  let restarts = 0;
  client.onRestart = () => { restarts += 1; };
  const result = await client.request('/api/jobs', { mode: 'calculate', prompt: '3+4' });
  assert.deepEqual(result, { id: 'job-one' });
  assert.deepEqual(calls.map(call => call.path), ['/api/jobs', '/api/bootstrap', '/api/jobs']);
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[2].options.method, 'POST');
  assert.equal(calls[0].options.body, calls[2].options.body);
  assert.equal(calls[2].options.headers['X-Prometheus-Token'], 'replacement');
  assert.equal(restarts, 1);
});

test('a second 403 is surfaced without a retry loop', async () => {
  const calls = [];
  const client = new LocalClient(async path => {
    calls.push(path);
    return path === '/api/bootstrap' ? response(200, { token: 'replacement' })
      : response(403, { error: 'Forbidden after renewal' });
  });
  client.token = 'expired';
  await assert.rejects(client.request('/api/jobs', { mode: 'models' }), error =>
    error.status === 403 && error.message === 'Forbidden after renewal');
  assert.deepEqual(calls, ['/api/jobs', '/api/bootstrap', '/api/jobs']);
});

test('404 propagates its status and message without bootstrap or replay', async () => {
  const calls = [];
  const client = new LocalClient(async path => {
    calls.push(path);
    return response(404, { error: 'Request no longer available.' });
  });
  client.token = 'valid';
  await assert.rejects(client.request('/api/job/missing'), error =>
    error.status === 404 && error.message === 'Request no longer available.');
  assert.deepEqual(calls, ['/api/job/missing']);
});

test('an uncertain network failure never automatically replays a POST', async () => {
  let acceptedCount = 0;
  const client = new LocalClient(async (path, options) => {
    assert.equal(path, '/api/jobs');
    assert.equal(options.method, 'POST');
    acceptedCount += 1; // Simulate server acceptance before the response is lost.
    throw new TypeError('Connection was interrupted after acceptance');
  });
  client.token = 'valid';
  await assert.rejects(client.request('/api/jobs', { mode: 'code_apply', content: 'change' }),
    /Connection was interrupted after acceptance/);
  assert.equal(acceptedCount, 1);
});

test('an aborted POST and malformed response are not replayed', async () => {
  for (const failure of [new DOMException('Request timed out', 'TimeoutError'), new SyntaxError('Invalid JSON')]) {
    let calls = 0;
    const client = new LocalClient(async () => {
      calls += 1;
      if (failure instanceof SyntaxError) return { status: 200, ok: true, json: async () => { throw failure; } };
      throw failure;
    });
    client.token = 'valid';
    await assert.rejects(client.request('/api/jobs', { mode: 'code_apply' }), error => error === failure);
    assert.equal(calls, 1);
  }
});

test('restart callback fires only on a changed nonempty token and sees the new token', async () => {
  const tokens = ['first', 'first', 'second', 'second', 'third'];
  const client = new LocalClient(async () => response(200, { token: tokens.shift() }));
  const changes = [];
  client.onRestart = () => changes.push(client.token);
  for (let i = 0; i < 5; i += 1) await client.bootstrap();
  assert.deepEqual(changes, ['second', 'third']);
});

test('failed bootstrap releases the shared attempt so a later request can reconnect', async () => {
  let calls = 0;
  const client = new LocalClient(async () => {
    calls += 1;
    return calls === 1 ? response(503, { error: 'Starting' }) : response(200, { token: 'recovered' });
  });
  await assert.rejects(client.bootstrap(), /Unable to reconnect/);
  assert.equal(client.bootstrapPromise, null);
  assert.equal(client.token, '');
  assert.deepEqual(await client.bootstrap(), { token: 'recovered' });
  assert.equal(calls, 2);
});

test('concurrent 403 renewals share an in-flight bootstrap and one restart callback', async () => {
  const renewed = deferred();
  const calls = [];
  const client = new LocalClient(async (path, options) => {
    calls.push(path);
    if (path === '/api/bootstrap') return renewed.promise;
    return options.headers['X-Prometheus-Token'] === 'old'
      ? response(403, { error: 'Expired' }) : response(200, { path });
  });
  client.token = 'old';
  let changes = 0;
  client.onRestart = () => { changes += 1; };
  const first = client.request('/api/status');
  const second = client.request('/api/sessions');
  // Advance fetch and JSON microtasks until both 403 handlers have reached bootstrap.
  for (let i = 0; i < 6; i += 1) await Promise.resolve();
  assert.equal(calls.filter(path => path === '/api/bootstrap').length, 1);
  renewed.resolve(response(200, { token: 'renewed' }));
  assert.deepEqual(await Promise.all([first, second]), [{ path: '/api/status' }, { path: '/api/sessions' }]);
  assert.equal(changes, 1);
  assert.equal(calls.filter(path => path === '/api/bootstrap').length, 1);
});
