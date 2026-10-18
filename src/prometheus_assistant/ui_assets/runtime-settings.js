(function () {
'use strict';
const el = id => document.getElementById(id);
let state = {busy: false, online: false, closed: false}, pending = null, sending = false, deciding = null;
const say = value => { el('runtimeSettingsStatus').textContent = value; };
function controls() {
  const locked = state.busy || !state.online || state.closed || sending;
  el('runtimeBackend').disabled = locked;
  el('reviewRuntimeSettings').disabled = locked || !!pending;
  el('refreshRuntimeSettings').disabled = locked;
  for (const id of ['approveRuntimeSettings', 'rejectRuntimeSettings']) el(id).disabled = locked || !pending;
}
function review(value, show = false) {
  pending = value?.kind === 'runtime_config' ? value : null;
  const box = el('runtimeSettingsReview'); box.replaceChildren(); box.hidden = !pending;
  el('runtimeSettingsDecisions').hidden = !pending;
  if (pending) {
    const data = pending.review || {};
    for (const [key, label] of [['action','Change'], ['reason','Why'], ['benefit','Benefit'],
      ['effects','Effects'], ['current_backend','Current preference'], ['proposed_backend','New preference'], ['duration','Duration']]) {
      const row = document.createElement('p'); row.textContent = label + ': ' + String(data[key] || ''); box.append(row);
    }
    say('Approve or deny this exact startup preference. Editing the selection does not change this review.');
    if (show) window.PrometheusWorkspace?.show('Maintenance');
  }
  controls();
}
async function request(body) {
  if (state.busy || !state.online || state.closed || sending) return false;
  sending = true; controls();
  try {
    const context = typeof session === 'string' && session ? {session} : {};
    const accepted = await submitAction({...context, ...body});
    if (!accepted) say('Request did not start. Check the current connection.');
    return accepted;
  } catch (error) { say(error.message || 'Runtime setting request failed.'); return false; }
  finally { sending = false; controls(); }
}
async function decide(decision) {
  if (!pending) return;
  deciding = pending.id;
  if (!await request({mode: 'approval_decision', approval_id: pending.id,
      approval_fingerprint: pending.fingerprint, approval_decision: decision, session: pending.session})) deciding = null;
}
el('reviewRuntimeSettings').onclick = () => request({mode:'runtime_config', runtime_backend:el('runtimeBackend').value});
el('refreshRuntimeSettings').onclick = async () => {
  if (state.busy || !state.online || state.closed || sending) return;
  try {
    const data = await api('/api/approvals/pending');
    if (state.closed || !state.online) return;
    review(data.approval);
    if (data.approval && !pending) say('Another change is awaiting approval. Review it before changing runtime settings.');
    else if (!pending) await request({mode:'runtime_status'});
  } catch (error) { say(error.message || 'Could not refresh runtime settings.'); }
};
el('approveRuntimeSettings').onclick = () => decide('approve');
el('rejectRuntimeSettings').onclick = () => decide('reject');
window.addEventListener('prometheus-busy', event => { state = {...state, ...event.detail}; controls(); });
window.addEventListener('prometheus-result', event => {
  if (state.closed) return;
  const result = event.detail || {}, data = result.details || {};
  if (result.origin === 'observed') return;
  if (data.approval_resolved && (data.approval_resolved === pending?.id || data.approval_resolved === deciding)) {
    review(null); deciding = null; say(result.reply || 'Runtime review resolved.');
  }
  if (data.approval) review(data.approval, data.approval.kind === 'runtime_config');
  if (data.runtime_settings) {
    const setting = data.runtime_settings;
    el('runtimeBackend').value = setting.backend;
    say((result.reply || 'Runtime settings refreshed.') + ' Saved preference: ' + setting.backend +
      '. Applies to future launches on this computer. The running backend is not established by this preference.');
  }
  if (result.error && (['runtime_config','runtime_status'].includes(result.action) || deciding)) {
    say(result.error + (deciding ? ' Refresh settings before preparing another review.' : ''));
    if (deciding) { review(null); deciding = null; }
  }
  controls();
});
window.addEventListener('prometheus-auth-lost', () => {
  state = {busy:false, online:false, closed:true}; deciding = null; review(null);
  say('Unlock Prometheus to inspect runtime settings.'); controls();
});
controls();
})();
