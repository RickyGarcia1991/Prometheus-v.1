(function () {
'use strict';
const element = id => document.getElementById(id);
const MAX_TEXT = 100000, MAX_JSON = 60000;
let local = {online: false, closed: false};
let state = {phase: 'disconnected', devices: [], tools: [], pending: null};
let requesting = false, epoch = 0, timer = null, loaded = false;
let catalog = '', fields = [], simple = false, reviewedId = null, consumedId = null;

function textNode(tag, text, className) {
  const node = document.createElement(tag); node.textContent = String(text ?? '');
  if (className) node.className = className;
  return node;
}
function message(text) { element('remoteStatus').textContent = text; }
function chosenTool() { return (state.tools || []).find(tool => tool.name === element('remoteTool').value); }
function chosenDevice() { return (state.devices || []).find(device => device.id === state.selected_device_id); }
function expires(pending) {
  if (!pending) return 0;
  return typeof pending.expires_at === 'number' ? pending.expires_at * 1000 : Date.parse(pending.expires_at);
}
function stopTimer() { if (timer !== null) clearTimeout(timer); timer = null; }
function ready() { return state.phase === 'ready' && chosenDevice()?.status === 'online'; }
function controls() {
  const unavailable = !local.online || local.closed || requesting;
  const moving = ['connecting', 'working'].includes(state.phase);
  const editable = !unavailable && !moving && ready();
  element('remoteConnect').disabled = unavailable || !['disconnected', 'error'].includes(state.phase);
  element('remoteRefresh').disabled = unavailable || moving || !['ready', 'error'].includes(state.phase);
  element('remoteDisconnect').disabled = unavailable || state.phase === 'disconnected';
  element('remoteDevice').disabled = unavailable || moving || state.phase !== 'ready';
  element('remoteTool').disabled = !editable || !(state.tools || []).length;
  element('remoteAdvanced').disabled = !editable || !simple || !chosenTool();
  element('remoteArguments').disabled = !editable || !chosenTool();
  fields.forEach(field => { field.input.disabled = !editable; });
  element('remotePrepare').disabled = !editable || !chosenTool();
  const pending = state.pending;
  element('remoteExecute').disabled = !editable || !pending || pending.id !== reviewedId
    || pending.id === consumedId || pending.device_id !== state.selected_device_id
    || !Number.isFinite(expires(pending)) || expires(pending) <= Date.now();
  element('remoteCancel').disabled = unavailable || moving || !pending;
}
function invalidate() {
  reviewedId = null;
  if (state.pending) element('remoteReviewExpiry').textContent = 'Arguments changed. Choose Review action again before running.';
  controls();
}
function showArgumentMode() {
  const advanced = element('remoteAdvanced').checked;
  element('remoteJsonPanel').hidden = !advanced;
  element('remoteFields').hidden = advanced;
  element('remoteArgumentsHelp').hidden = advanced;
}
function schemaFields(tool) {
  const schema = tool?.inputSchema;
  if (!schema || schema.type !== 'object' || !schema.properties || typeof schema.properties !== 'object'
      || Array.isArray(schema.properties) || ['oneOf', 'anyOf', 'allOf', '$ref'].some(key => key in schema)) return null;
  const entries = Object.entries(schema.properties).filter(([name]) => name !== 'deviceId');
  if (entries.length > 14) return null;
  if (entries.some(([, prop]) => !prop || typeof prop !== 'object'
      || !['string', 'integer', 'number', 'boolean'].includes(prop.type)
      || ['oneOf', 'anyOf', 'allOf', '$ref'].some(key => key in prop)
      || (prop.enum && (!Array.isArray(prop.enum) || prop.enum.some(value => !['string', 'number', 'boolean'].includes(typeof value)))))) return null;
  return entries;
}
function renderFields() {
  invalidate(); fields = [];
  const holder = element('remoteFields'); holder.replaceChildren();
  const tool = chosenTool(), entries = schemaFields(tool);
  element('remoteToolDescription').textContent = String(tool?.description || '').slice(0, 8000);
  simple = entries !== null;
  element('remoteAdvanced').checked = !!tool && !simple;
  element('remoteArguments').value = '{}';
  if (entries) for (const [name, schema] of entries) {
    const required = (tool.inputSchema.required || []).includes(name);
    const label = textNode('label', `${schema.title || name}${required ? ' (required)' : ' (optional)'}`);
    const id = 'remoteField' + fields.length;
    label.setAttribute('for', id);
    let input;
    if (schema.enum || schema.type === 'boolean') {
      input = document.createElement('select');
      input.append(new Option(required ? 'Choose a value…' : 'Not provided', ''));
      for (const value of schema.enum || [true, false]) input.append(new Option(String(value), JSON.stringify(value)));
      if ('default' in schema) input.value = JSON.stringify(schema.default);
    } else {
      input = document.createElement(schema.type === 'string' ? 'textarea' : 'input');
      if (schema.type !== 'string') { input.type = 'number'; input.step = schema.type === 'integer' ? '1' : 'any'; }
      else { input.rows = 2; input.maxLength = MAX_JSON; input.spellcheck = false; }
      input.autocomplete = 'off';
      if ('default' in schema) input.value = String(schema.default);
    }
    input.setAttribute('id', id); input.addEventListener('input', invalidate); input.addEventListener('change', invalidate);
    label.append(input); holder.append(label);
    if (schema.description) holder.append(textNode('p', String(schema.description).slice(0, 2000), 'fine'));
    fields.push({name, schema, required, input});
  }
  showArgumentMode(); controls();
}
function argumentsFromForm() {
  let args = Object.create(null);
  if (element('remoteAdvanced').checked) {
    const text = element('remoteArguments').value;
    if (text.length > MAX_JSON) throw new Error('Arguments exceed the 60,000-character limit.');
    try { args = JSON.parse(text); } catch { throw new Error('Enter a valid JSON object before reviewing.'); }
    if (!args || Array.isArray(args) || typeof args !== 'object') throw new Error('Arguments must be a JSON object.');
    if (Object.hasOwn(args, 'deviceId')) throw new Error('Remove deviceId from the JSON. Select the computer using the Computer menu.');
  } else for (const field of fields) {
    const value = field.input.value;
    if (value === '') {
      if (field.required) throw new Error('Enter a value for ' + field.name + '.');
      continue;
    }
    if (field.schema.enum || field.schema.type === 'boolean') args[field.name] = JSON.parse(value);
    else if (field.schema.type === 'number' || field.schema.type === 'integer') {
      const number = Number(value);
      if (!Number.isFinite(number) || (field.schema.type === 'integer' && !Number.isInteger(number))) throw new Error('Enter a valid ' + field.schema.type + ' for ' + field.name + '.');
      args[field.name] = number;
    } else args[field.name] = value;
  }
  if (JSON.stringify(args).length > MAX_JSON) throw new Error('Arguments exceed the 60,000-character limit.');
  return args;
}
function renderReview() {
  const pending = state.pending;
  element('remoteReview').hidden = !pending;
  if (!pending) { element('remotePreview').textContent = ''; element('remoteReviewTarget').textContent = ''; return; }
  element('remoteReviewTarget').textContent = `${pending.device_name || 'Computer'}\nDevice ID: ${pending.device_id}\nAction: ${pending.tool}`;
  // Never truncate the arguments being approved. The backend bounds the preview.
  element('remotePreview').textContent = JSON.stringify(pending.arguments, null, 2);
  const expiry = expires(pending);
  element('remoteReviewExpiry').textContent = pending.id === consumedId
    ? 'This preview was already submitted. Check the result; it will not be submitted again.'
    : !Number.isFinite(expiry) || expiry <= Date.now() ? 'This preview has expired. Review the action again.'
    : pending.id !== reviewedId ? 'This preview was not prepared in this tab. Choose Review action to prepare your current inputs.'
    : 'Review expires at ' + new Date(expiry).toLocaleTimeString() + '. Running consumes this preview once.';
}
function renderResult(result) {
  const holder = element('remoteResult'); holder.replaceChildren();
  if (result === null || result === undefined) { holder.append(textNode('p', 'No remote action result yet.', 'fine')); return; }
  holder.append(textNode('p', result.isError ? 'The provider reported an action error.' : 'Remote action result', 'fine'));
  let remaining = MAX_TEXT, images = 0;
  function text(value) {
    const raw = String(value), shown = raw.slice(0, remaining);
    remaining -= shown.length;
    if (shown) holder.append(textNode('pre', shown, 'code-output'));
    if (raw.length > shown.length) holder.append(textNode('p', 'Additional text omitted by the display limit.', 'fine'));
  }
  if (Array.isArray(result.content)) for (const item of result.content.slice(0, 30)) {
    if (item?.type === 'text') text(item.text || '');
    else if (item?.type === 'image' && ['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(item.mimeType)
      && typeof item.data === 'string' && item.data.length <= 2800000 && /^[A-Za-z0-9+/]+={0,2}$/.test(item.data) && images < 3) {
      const image = document.createElement('img'); image.alt = 'Image returned by the selected computer';
      image.src = 'data:' + item.mimeType + ';base64,' + item.data;
      image.addEventListener('error', () => { image.remove(); holder.append(textNode('p', 'The returned image could not be displayed.', 'fine')); });
      holder.append(image); images++;
    } else holder.append(textNode('p', 'A file, image or other non-text result is unavailable in this panel. Use a supported text action to inspect it.', 'fine'));
  } else text(typeof result === 'string' ? result : JSON.stringify(result, null, 2));
  if (result.content?.length > 30) holder.append(textNode('p', 'Additional result items omitted by the display limit.', 'fine'));
}
function schedule() {
  stopTimer();
  if (!local.online || local.closed) return;
  if (['connecting', 'working'].includes(state.phase)) timer = setTimeout(load, 1500);
  else if (state.pending && expires(state.pending) > Date.now()) timer = setTimeout(() => { timer = null; renderReview(); controls(); }, Math.min(expires(state.pending) - Date.now() + 25, 2147483647));
}
function render(data, refreshHealth = false) {
  const healthChanged = refreshHealth || state.phase !== data.phase
    || state.selected_device_id !== data.selected_device_id
    || JSON.stringify(state.devices) !== JSON.stringify(data.devices);
  state = data; loaded = true;
  message(String(data.message || data.phase || 'Connection state updated.'));
  const deviceSelect = element('remoteDevice');
  deviceSelect.replaceChildren(new Option('Choose a computer…', ''));
  for (const device of data.devices || []) {
    const option = new Option(`${device.name || 'Computer'} · ${device.status || 'unknown'} · ${device.id}`, device.id);
    option.disabled = device.status !== 'online'; deviceSelect.append(option);
  }
  deviceSelect.value = data.selected_device_id || '';
  const device = chosenDevice();
  element('remoteDeviceInfo').textContent = device
    ? `${device.name || 'Computer'} · ${device.status || 'unknown'}\nDevice ID: ${device.id}`
    : (data.devices || []).length ? 'Choose an online computer. Offline and unknown devices cannot run actions.' : 'No paired computers listed. Complete the agent setup on the target computer, then refresh.';
  const nextCatalog = JSON.stringify(data.tools || []);
  if (catalog !== nextCatalog) {
    catalog = nextCatalog;
    const old = element('remoteTool').value;
    element('remoteTool').replaceChildren(new Option('Choose an action…', ''));
    for (const tool of data.tools || []) element('remoteTool').append(new Option(tool.name, tool.name));
    if ((data.tools || []).some(tool => tool.name === old)) element('remoteTool').value = old;
    renderFields();
  }
  if (data.pending?.id !== reviewedId) reviewedId = null;
  renderReview(); renderResult(data.result); controls(); schedule();
  if (healthChanged) window.dispatchEvent(new CustomEvent('prometheus-remote-state-updated'));
}
async function load() {
  if (requesting || !local.online || local.closed) return;
  const current = ++epoch; requesting = true; stopTimer(); controls();
  try { const data = await api('/api/remote/state'); if (current === epoch) render(data); }
  catch (error) {
    if (current === epoch) {
      message(error.message || 'Could not read the local remote-access state.'); stopTimer();
      if (['connecting', 'working'].includes(state.phase)) timer = setTimeout(load, 4000);
    }
  }
  finally { if (current === epoch) { requesting = false; controls(); } }
}
async function action(body) {
  if (requesting || !local.online || local.closed) return;
  const current = ++epoch; requesting = true; stopTimer(); controls();
  message(body.action === 'execute' ? 'Submitting the reviewed action once…' : 'Updating connected computers…');
  try {
    const data = await api('/api/remote/action', body);
    if (current !== epoch) return;
    if (body.action === 'prepare') reviewedId = data.pending?.id || null;
    render(data, body.action === 'refresh');
  } catch (error) {
    if (current !== epoch) return;
    reviewedId = null;
    if (body.action === 'select') element('remoteDevice').value = state.selected_device_id || '';
    message((error.message || 'The request did not complete.') + (body.action === 'execute'
      ? ' Its outcome is uncertain. Refresh the connection state before preparing another action; this action was not retried.' : ''));
    if (body.action === 'execute') timer = setTimeout(load, 1500);
  } finally { if (current === epoch) { requesting = false; controls(); } }
}

element('remoteConnect').onclick = () => action({action: 'connect'});
element('remoteRefresh').onclick = () => action({action: 'refresh'});
element('remoteDisconnect').onclick = () => { reviewedId = null; return action({action: 'disconnect'}); };
element('remoteDevice').onchange = () => {
  invalidate(); const device = (state.devices || []).find(item => item.id === element('remoteDevice').value);
  if (!device || device.status !== 'online') { message('Choose an online computer.'); return; }
  return action({action: 'select', device_id: device.id});
};
element('remoteTool').onchange = renderFields;
element('remoteArguments').oninput = invalidate;
element('remoteAdvanced').onchange = () => {
  invalidate();
  if (element('remoteAdvanced').checked) {
    element('remoteAdvanced').checked = false;
    try { element('remoteArguments').value = JSON.stringify(argumentsFromForm(), null, 2); } catch { element('remoteArguments').value = '{}'; }
    element('remoteAdvanced').checked = true;
  }
  showArgumentMode();
};
element('remotePrepare').onclick = () => {
  if (!ready() || !chosenTool()) return;
  try { return action({action: 'prepare', tool: chosenTool().name, arguments: argumentsFromForm()}); }
  catch (error) { invalidate(); message(error.message); }
};
element('remoteExecute').onclick = () => {
  controls(); if (element('remoteExecute').disabled) return;
  const pending_id = state.pending.id; consumedId = pending_id; reviewedId = null;
  return action({action: 'execute', pending_id});
};
element('remoteCancel').onclick = () => { reviewedId = null; return action({action: 'cancel'}); };
element('tabRemote').addEventListener('click', load);
element('tabRemote').addEventListener('focus', () => { if (!loaded) load(); });
window.addEventListener('prometheus-busy', event => {
  const wasOnline = local.online;
  local = {...local, ...event.detail};
  if (!local.online || local.closed) {
    epoch++; requesting = false; reviewedId = null; loaded = false; stopTimer();
    message(local.closed ? 'Prometheus is closed.' : 'The local Prometheus connection is unavailable.');
  } else if (!wasOnline && !element('panelRemote').hidden) load();
  controls();
});
window.addEventListener('beforeunload', stopTimer);
controls();
})();
