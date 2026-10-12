'use strict';
(() => {
  const status = document.getElementById('ownerStatus');
  const enroll = document.getElementById('ownerEnroll');
  const unlock = document.getElementById('ownerUnlock');
  const verify = document.getElementById('ownerVerify');
  const returnLink = document.getElementById('ownerReturn');
  let token = '', setupTicket = new URLSearchParams(location.hash.slice(1)).get('setup') || '';
  let state = null, busy = false;
  // The one-time setup ticket is never written into history, DOM, logs or storage.
  if (location.hash) history.replaceState(null, '', location.pathname);
  const decode = s => Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')), c=>c.charCodeAt(0));
  const encode = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
  async function api(path, body) {
    const response = await fetch(path, {method:body ? 'POST':'GET', cache:'no-store', headers:{'Content-Type':'application/json','X-Prometheus-Token':token}, ...(body?{body:JSON.stringify(body)}:{})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Verification failed.');
    return result;
  }
  function render() {
    const ready = !!state?.required && !!state.ready && !!window.PublicKeyCredential && !!navigator.credentials;
    const unlocked = ready && !!state.unlocked;
    document.getElementById('ownerTitle').textContent = unlocked ? 'Owner passkeys' : 'Unlock Prometheus';
    enroll.hidden = !ready || !(unlocked || (!state.enrolled && setupTicket));
    enroll.textContent = unlocked ? 'Add a backup passkey' : 'Set up owner passkey';
    enroll.disabled = busy || enroll.hidden;
    unlock.hidden = unlocked;
    unlock.disabled = busy || !ready || !state.enrolled || unlocked;
    verify.hidden = !unlocked;
    verify.disabled = busy || !unlocked;
    returnLink.hidden = !unlocked;
  }
  function stateMessage() {
    if (!state?.ready) return 'Verification components need repair. Access remains locked.';
    if (!window.PublicKeyCredential || !navigator.credentials) return 'Open this localhost page in a current Edge or Chrome browser to use a passkey.';
    if (state.unlocked) return 'Your workspace is unlocked. Add a backup passkey, return to chat, or lock the workspace to test a passkey.';
    if (state.enrolled) return 'Your workspace is locked. Choose Unlock with passkey.';
    return setupTicket ? 'Create your owner passkey, then verify it to unlock.' : 'First-time setup: open the private owner setup launcher on your main computer.';
  }
  async function refreshState(announce = false) {
    const previous = JSON.stringify(state);
    try {
      const b = await api('/api/bootstrap'); token = b.token; state = b.owner_access;
      if (!state || typeof state.required !== 'boolean') throw new Error('Unable to confirm owner access. Reload this page to reconnect.');
      if (!state.required) { location.replace('/'); return; }
      // This supported read also keeps the waiting owner page alive without
      // extending the authenticated session or opening a credential prompt.
      state = await api('/api/owner/state');
      render();
      if (announce || previous !== JSON.stringify(state)) status.textContent = stateMessage();
    } catch (error) { state = null; render(); throw error; }
  }
  function serial(credential) {
    const r = credential.response;
    const response = {clientDataJSON:encode(r.clientDataJSON)};
    if (r.attestationObject) response.attestationObject = encode(r.attestationObject);
    if (r.authenticatorData) response.authenticatorData = encode(r.authenticatorData);
    if (r.signature) response.signature = encode(r.signature);
    if (r.userHandle) response.userHandle = encode(r.userHandle);
    return {id:credential.id,rawId:encode(credential.rawId),type:credential.type,response,clientExtensionResults:credential.getClientExtensionResults()};
  }
  async function perform(kind) {
    if (busy || (kind === 'register' ? enroll.disabled || enroll.hidden : unlock.disabled || unlock.hidden)) return;
    busy = true; render();
    try {
      const begin = await api('/api/owner/begin', {kind, ...(kind==='register'?{setup_ticket:setupTicket}:{})});
      const options = begin.options.publicKey;
      options.challenge = decode(options.challenge);
      if (options.user) options.user.id = decode(options.user.id);
      for (const field of ['allowCredentials','excludeCredentials']) for (const row of options[field] || []) row.id = decode(row.id);
      status.textContent = 'Complete the verification in your device’s sign-in window.';
      const credential = kind==='register' ? await navigator.credentials.create({publicKey:options}) : await navigator.credentials.get({publicKey:options});
      if (!credential) throw new Error('Verification cancelled.');
      const result = await api('/api/owner/complete', {request_id:begin.request_id,response:serial(credential)});
      if (result.unlocked) { location.replace('/'); return; }
      if (!result.registered) throw new Error('The passkey was not confirmed as saved. Check owner access before trying again.');
      setupTicket = '';
      await refreshState();
      status.textContent = state?.unlocked
        ? 'Backup passkey saved. Return to chat, or choose Lock to test a passkey, then select the new device when unlocking.'
        : 'Passkey saved. Choose Unlock with passkey to verify it.';
    } catch (error) {
      // An uncertain POST is never replayed. Refresh only public state so the
      // controls reflect a cancelled first setup or an expired owner session.
      await refreshState().catch(() => {});
      status.textContent = error.name==='NotAllowedError' ? 'Sign-in was cancelled or unavailable. Retry with your PIN, another browser, or a security key.' : error.message;
    } finally { busy = false; render(); }
  }
  unlock.addEventListener('click',()=>perform('authenticate'));
  enroll.addEventListener('click',()=>perform('register'));
  verify.addEventListener('click', async () => {
    if (busy || verify.disabled || verify.hidden) return;
    busy = true; render();
    try {
      await api('/api/owner/lock', {});
      await refreshState();
      status.textContent = 'Workspace locked. Choose Unlock with passkey and select the phone or security key you want to test.';
    } catch (error) {
      await refreshState().catch(() => {});
      status.textContent = error.message;
    } finally { busy = false; render(); }
  });
  async function init() {
    try {
      await refreshState(true);
    } catch (error) { status.textContent = error.message; }
  }
  init();
  setInterval(() => { if (!busy) refreshState().catch(error => { status.textContent = error.message; }); },60000);
})();
