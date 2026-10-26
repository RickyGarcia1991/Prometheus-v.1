'use strict';
(() => {
  const status = document.getElementById('ownerStatus');
  const enroll = document.getElementById('ownerEnroll');
  const unlock = document.getElementById('ownerUnlock');
  const verify = document.getElementById('ownerVerify');
  const returnLink = document.getElementById('ownerReturn');
  const pinSection = document.getElementById('pinUnlockSection');
  const pinManage = document.getElementById('pinManageSection');
  const pinForm = document.getElementById('pinUnlockForm');
  const pinSetupForm = document.getElementById('pinSetupForm');
  const pinUnlock = document.getElementById('pinUnlock');
  const pinSave = document.getElementById('pinSave');
  const pinRemove = document.getElementById('pinRemove');
  const pinAttempts = document.getElementById('pinAttempts');
  const pinManagementStatus = document.getElementById('pinManagementStatus');
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
    const ready = !!state?.required && !!state.ready;
    const webauthn = !!window.PublicKeyCredential && !!navigator.credentials;
    const unlocked = ready && !!state.unlocked;
    const passkeyVerified = !!state?.passkey_verified;
    document.getElementById('ownerTitle').textContent = unlocked ? 'Owner sign-in settings' : 'Unlock Prometheus';
    enroll.hidden = !ready || !(passkeyVerified || (!state.enrolled && setupTicket));
    enroll.textContent = unlocked ? 'Add a backup passkey' : 'Set up owner passkey';
    enroll.disabled = busy || enroll.hidden || !webauthn;
    unlock.hidden = passkeyVerified;
    unlock.textContent = unlocked ? 'Verify with passkey' : 'Unlock with passkey';
    unlock.disabled = busy || !ready || !state.enrolled || passkeyVerified || !webauthn;
    verify.hidden = !unlocked;
    verify.disabled = busy || !unlocked;
    returnLink.hidden = !unlocked;
    verify.textContent = 'Lock to test sign-in';
    const pin = state?.pin || {};
    pinSection.hidden = !state?.ready || unlocked || !pin.configured;
    pinUnlock.disabled = busy || pinSection.hidden || !!pin.locked || (pin.retry_after || 0) > 0;
    pinAttempts.textContent = pin.locked ? 'PIN locked. Sign in with your passkey to restore PIN access.'
      : pin.retry_after > 0 ? 'Wait '+pin.retry_after+' seconds before trying again.'
      : (pin.remaining ?? 5)+' incorrect attempts remain before PIN lockout.';
    pinManage.hidden = !ready || !passkeyVerified;
    pinSave.disabled = busy || pinManage.hidden;
    pinSave.textContent = pin.configured ? 'Change backup PIN' : 'Save backup PIN';
    pinRemove.hidden = !pin.configured;
    pinRemove.disabled = busy || pinManage.hidden;
    document.getElementById('pinReverify').hidden = !unlocked || passkeyVerified;
  }
  function stateMessage() {
    if (!state?.ready) return 'Verification components need repair. Access remains locked.';
    if ((!window.PublicKeyCredential || !navigator.credentials) && !state.pin?.configured) return 'Open this localhost page in a current Edge or Chrome browser to use a passkey.';
    if (state.unlocked) return 'Your workspace is unlocked. Manage your sign-in methods, return to chat, or lock the workspace to test sign-in.';
    if (state.enrolled) return state.pin?.configured ? 'Choose your passkey or backup PIN.' : 'Your workspace is locked. Choose Unlock with passkey. You can set a backup PIN after signing in.';
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
      if (result.unlocked) {
        if (location.pathname === '/owner-access') { await refreshState(true); return; }
        location.replace('/'); return;
      }
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
  pinForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || pinUnlock.disabled || pinSection.hidden) return;
    busy = true; render();
    const input = document.getElementById('backupPin');
    try {
      const request = api('/api/owner/pin/unlock', {pin:input.value});
      input.value = '';
      const result = await request;
      if (!result.unlocked) throw new Error('PIN sign-in was not confirmed.');
      location.replace('/');
    } catch (error) {
      await refreshState().catch(()=>{});
      status.textContent = error.message;
    } finally { input.value = ''; busy = false; render(); }
  });
  pinSetupForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || pinSave.disabled || pinManage.hidden) return;
    busy = true; render();
    const first = document.getElementById('newBackupPin');
    const second = document.getElementById('confirmBackupPin');
    try {
      const request = api('/api/owner/pin/setup', {pin:first.value, confirmation:second.value});
      first.value = ''; second.value = '';
      await request;
      await refreshState();
      pinManagementStatus.textContent = 'Backup PIN saved. Choose Lock to test sign-in, then unlock using your PIN.';
    } catch (error) { pinManagementStatus.textContent = error.message; }
    finally { first.value = ''; second.value = ''; busy = false; render(); }
  });
  pinRemove.addEventListener('click', async () => {
    if (busy || pinRemove.disabled || pinManage.hidden) return;
    busy = true; render();
    try {
      await api('/api/owner/pin/disable', {});
      await refreshState();
      pinManagementStatus.textContent = 'Backup PIN removed. Your passkeys still work.';
    } catch (error) { pinManagementStatus.textContent = error.message; }
    finally { busy = false; render(); }
  });
  verify.addEventListener('click', async () => {
    if (busy || verify.disabled || verify.hidden) return;
    busy = true; render();
    try {
      await api('/api/owner/lock', {});
      await refreshState();
      status.textContent = 'Workspace locked. Test your backup PIN or choose Unlock with passkey.';
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
  setInterval(() => { if (!busy) refreshState().catch(error => { status.textContent = error.message; }); },5000);
})();
