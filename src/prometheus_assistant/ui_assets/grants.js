(function () {
'use strict';
const el = id => document.getElementById(id);
let state = {online: false, busy: false, closed: false, authLost: false}, epoch = 0, pending = null, working = false;
const inputs = ['grantCapability', 'grantArguments', 'grantSeconds', 'grantUses'];
function usable() { return state.online && !state.closed && !state.authLost; }
function controls() {
  for (const id of inputs) el(id).disabled = !usable() || working;
  el('grantReview').disabled = !usable() || working;
  el('grantIssue').disabled = !usable() || working || !pending;
  el('grantRefresh').disabled = !usable() || working;
  el('grantRunId').disabled = !usable() || working;
  el('grantRun').disabled = !usable() || working || state.busy;
}
function invalidate() { epoch++; pending = null; el('grantReviewText').textContent = ''; controls(); }
function clear(message) {
  invalidate(); working = false;
  el('grantArguments').value = '{}'; el('grantRunId').value = '';
  el('grantRows').replaceChildren(); el('grantStatus').textContent = message; controls();
}
function request() {
  const [worker, tool] = el('grantCapability').value.split('/');
  return {worker, tool, arguments: JSON.parse(el('grantArguments').value),
    seconds: Number(el('grantSeconds').value), uses: Number(el('grantUses').value)};
}
async function action(callback) {
  if (!usable() || working) return;
  working = true; const current = epoch; controls();
  try { await callback(() => current === epoch && usable()); }
  catch (error) { if (current === epoch) { pending = null; el('grantStatus').textContent = error.message || 'Grant action failed.'; } }
  finally { if (current === epoch) { working = false; controls(); } }
}
async function inspect(valid) {
  const data = await api('/api/permissions/grants');
  if (!valid()) return;
  const rows = data.grants.map(grant => {
    const card = document.createElement('div'); card.className = 'workspace-card';
    const text = document.createElement('p');
    text.textContent = grant.capability + ' · target: ' + grant.target + ' · ' + grant.state +
      ' · attempts left: ' + grant.remaining + ' · expires: ' + new Date(grant.expires * 1000).toISOString() +
      '\nID: ' + grant.id + '\nExact arguments digest: ' + grant.scope;
    const revoke = document.createElement('button'); revoke.textContent = 'Revoke this grant';
    revoke.disabled = grant.state !== 'active';
    revoke.onclick = () => action(async fresh => {
      await api('/api/permissions/grants/revoke', {id: grant.id});
      if (fresh()) { el('grantStatus').textContent = 'Grant revoked.'; await inspect(fresh); }
    });
    card.append(text, revoke); return card;
  });
  el('grantRows').replaceChildren(...rows);
  if (!rows.length) el('grantStatus').textContent = 'No grants have been issued for this owner on this host.';
}
for (const id of inputs) el(id).oninput = invalidate;
el('grantCapability').onchange = () => {
  el('grantArguments').value = el('grantCapability').value === 'project/tests' ? '{"target":"tests"}' : '{"provider":"crossref","query":"example topic"}';
  invalidate();
};
el('grantReview').onclick = () => action(async valid => {
  pending = null; const exact = request();
  const review = await api('/api/permissions/grants/review', exact);
  if (!valid()) return;
  pending = {review, exact};
  el('grantReviewText').textContent = 'Review before creating:\n' + exact.worker + '/' + exact.tool +
    '\nTarget: ' + review.target + '\nExact arguments: ' + JSON.stringify(exact.arguments, null, 2) +
    '\nDuration: ' + review.seconds + ' seconds; attempts: ' + review.uses +
    '\nPolicy: ' + review.policy_revision + '\n' + review.notice;
  el('grantStatus').textContent = 'No grant exists yet. Create reviewed grant authorizes only this exact scope.';
});
el('grantIssue').onclick = () => action(async valid => {
  if (!pending) return;
  const chosen = pending; pending = null;
  const grant = await api('/api/permissions/grants/issue', {review_id: chosen.review.review_id, scope_sha256: chosen.review.scope_sha256});
  if (!valid()) return;
  el('grantRunId').value = grant.id;
  el('grantStatus').textContent = 'Grant created; no action has run.';
  await inspect(valid);
});
el('grantRefresh').onclick = () => action(inspect);
el('grantRun').onclick = () => action(async valid => {
  if (state.busy) return;
  const exact = request();
  const job = await api('/api/jobs', {mode: 'core', worker: exact.worker, tool: exact.tool,
    arguments: exact.arguments, grant_id: el('grantRunId').value,
    invocation_id: crypto.randomUUID().replaceAll('-', '')});
  if (valid()) el('grantStatus').textContent = 'Exact action queued. Job: ' + job.id + '.';
  const deadline = Date.now() + 150000;
  while (valid() && Date.now() < deadline) {
    const row = await api('/api/job/' + job.id);
    if (!valid()) return;
    if (row.state !== 'running') {
      el('grantStatus').textContent = row.state === 'done' ? 'Exact action completed.' : (row.error || 'Action did not complete.');
      el('grantReviewText').textContent = row.result?.reply || row.error || '';
      await inspect(valid); return;
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  if (valid()) el('grantStatus').textContent = 'Outcome is still unconfirmed. Do not repeat the action; inspect the job before proceeding.';
});
el('ownerLock').addEventListener('click', () => { state.authLost = true; clear('Owner lock requested. Unlock again to manage grants.'); });
window.addEventListener('prometheus-auth-lost', () => { state.authLost = true; clear('Owner session ended.'); });
window.addEventListener('prometheus-busy', event => {
  state = {...state, ...event.detail};
  if (!usable()) clear('Grant controls are unavailable until the owner connection is restored.');
  controls();
});
controls();
})();
