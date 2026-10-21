(function () {
'use strict';
const element = id => document.getElementById(id);
const panel = element('workbench');
const main = panel.closest('main');
let state = {busy: false, online: false, closed: false};
let preparing = false, pending = null, selectedProject = '', opened = null, reviewed = null, lastEdit = null;

function message(text) { element('workspaceStatus').textContent = text; }
function dirty() { return !!opened && element('codeEditor').value !== opened.content; }
function projectMatches() { return !!selectedProject && element('projectRoot').value.trim() === selectedProject; }
function textNode(tag, text, className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className;
  return node;
}
function jsonResult(id, value) {
  element(id).replaceChildren(textNode('pre', JSON.stringify(value, null, 2).slice(0, 24000), 'code-output'));
}
function controls() {
  const locked = state.busy || !state.online || state.closed || preparing;
  panel.querySelectorAll('[data-work-action]').forEach(button => { button.disabled = locked; });
  panel.querySelectorAll('input, select').forEach(input => { input.disabled = locked; });
  const fileReady = projectMatches() && !!opened;
  element('codeEditor').disabled = locked || !fileReady;
  element('readCode').disabled = locked || !projectMatches() || !element('codeFile').value;
  element('previewCode').disabled = locked || !fileReady || !dirty();
  element('applyCode').disabled = locked || !fileReady || !reviewed || reviewed.content !== element('codeEditor').value;
  element('discardCode').disabled = locked || !opened || !dirty();
  element('undoCode').disabled = locked || !lastEdit || lastEdit.project !== selectedProject || !projectMatches() || dirty();
  element('runCodeTests').disabled = locked || !projectMatches() || !element('testPreset').value || dirty();
  element('importDocument').disabled = locked || !element('documentFile').files.length;
  element('enableWatch').disabled = locked || !element('watchProvider').value;
  element('disableWatch').disabled = locked || !element('watchProvider').value;
}
function showWorkbench(show) {
  panel.hidden = !show; main.classList.toggle('workspace-open', show);
  element('openWorkbench').setAttribute('aria-expanded', String(show));
  if (show) element('workbench').querySelector('[role=tab][aria-selected=true]').focus();
  else element('prompt').focus();
}
function switchPanel(name, focus = false) {
  ['Documents', 'Code', 'Maintenance'].forEach(item => {
    const selected = item === name, tab = element('tab' + item);
    tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
    element('panel' + item).hidden = !selected;
    if (selected && focus) tab.focus();
  });
  showWorkbench(true);
}
async function action(body, context = {}) {
  if (state.busy || !state.online || state.closed || preparing) {
    message(state.online ? 'Wait for the current request to finish.' : 'Reconnect to Prometheus to continue.');
    return false;
  }
  pending = {...context, mode: body.mode};
  message('Working on ' + body.mode.replaceAll('_', ' ') + '…');
  try {
    const accepted = await submitAction(body);
    if (!accepted) { pending = null; message('The request was not started. Check the connection and try again.'); }
    return accepted;
  } catch (error) { pending = null; message(error.message || 'The request could not start.'); return false; }
  finally { controls(); }
}
function projectBody(mode, extra = {}) { return {mode, project: element('projectRoot').value.trim(), ...extra}; }
function preserveDraft() {
  if (!dirty()) return false;
  message('Your editor has unapplied changes. Preview and apply them, or use Discard editor changes before switching files or projects.');
  return true;
}
function invalidatePreview() {
  reviewed = null; element('codeDiffPanel').hidden = true;
  element('codeFileStatus').textContent = opened ? (dirty() ? '· unapplied editor changes' : '· matches opened file') : '';
  controls();
}
function syncProviders() {
  const previous = element('watchProvider').value;
  const options = [...element('provider').options];
  if (options.length) {
    element('watchProvider').replaceChildren(...options.map(item => new Option(item.text, item.value)));
    if (options.some(item => item.value === previous)) element('watchProvider').value = previous;
  }
  controls();
}
function renderDocuments(rows) {
  const holder = element('documentList'); holder.replaceChildren();
  const old = element('documentVersion').value, seen = new Set();
  element('documentVersion').replaceChildren(new Option('New document / match filename', ''));
  if (!rows.length) holder.append(textNode('p', 'No documents imported yet.', 'fine'));
  for (const row of rows) {
    const card = textNode('div', '', 'workspace-card');
    card.append(textNode('strong', row.title || row.original_filename || 'Document'));
    card.append(textNode('p', `Version ${row.version_id} · ${row.is_latest ? 'latest saved' : 'historical'} · source date ${row.source_date || 'unknown'} · ${row.passage_count || 0} passages`, 'fine'));
    if (row.source_url) card.append(textNode('p', row.source_url, 'fine source-location'));
    holder.append(card);
    if (!seen.has(row.document_id)) {
      seen.add(row.document_id); element('documentVersion').add(new Option(row.title || row.original_filename, row.document_id));
    }
  }
  if (seen.has(old)) element('documentVersion').value = old;
}
function renderSearch(data) {
  const holder = element('documentResults'); holder.replaceChildren();
  holder.append(textNode('p', `Retrieval: ${data.retrieval || 'keyword'} · meaning search: ${data.semantic_status || 'not available'}`, 'fine'));
  if (!data.results?.length) holder.append(textNode('p', 'No matching passage found. Try another phrase or include historical versions.', 'fine'));
  for (const row of data.results || []) {
    const card = textNode('article', '', 'workspace-card');
    card.append(textNode('strong', row.title || 'Document'));
    card.append(textNode('p', `${row.location || 'Source passage'} · version ${row.version_id} · ${row.is_latest ? 'latest saved' : 'historical'} · source date ${row.source_date || 'unknown'}`, 'fine'));
    card.append(textNode('p', row.excerpt || '', 'document-excerpt'));
    if (row.source_url) card.append(textNode('p', row.source_url, 'fine source-location'));
    holder.append(card);
  }
}
function renderWatches(data) {
  const holder = element('sourceWatches'); holder.replaceChildren();
  const rows = data?.watches || [];
  if (!rows.length) holder.append(textNode('p', 'No watched queries saved yet.', 'fine'));
  for (const row of rows) {
    const card = textNode('div', '', 'workspace-card');
    card.append(textNode('strong', `${row.provider} · ${row.query}`));
    card.append(textNode('p', `${row.enabled ? 'Enabled' : 'Disabled'} · ${row.due ? 'due now' : 'next due ' + (row.next_due_utc || 'unknown')} · last successful check ${row.last_success_utc || 'not yet'}`, 'fine'));
    if (row.error) card.append(textNode('p', row.error, 'fine'));
    const choose = textNode('button', 'Use this query', 'quiet');
    choose.type = 'button';
    choose.onclick = () => {
      if (state.busy || state.closed || !state.online) return;
      element('watchProvider').value = row.provider; element('watchQuery').value = row.query;
      message('Selected this exact publisher and query. Choose Enable or Disable to change it.');
    };
    choose.dataset.workAction = ''; card.append(choose); holder.append(card);
  }
}
function renderReadiness(data) {
  const holder = element('readinessResults'); holder.replaceChildren();
  const report = data.readiness || {}, hw = report.hardware || {};
  holder.append(textNode('p', `${hw.ram_gib ?? '?'} GiB installed · ${hw.available_ram_gib ?? '?'} GiB available · ${report.data_disk?.free_gib ?? '?'} GiB free on data drive`, 'fine'));
  if (report.devices) holder.append(textNode('p', `Audio inputs: ${report.devices.microphone_input_devices ?? 'unknown'} · audio outputs: ${report.devices.speaker_output_devices ?? 'unknown'}. Presence does not confirm a working microphone or speaker.`, 'fine'));
  if (report.manual_checks_remaining?.length) {
    holder.append(textNode('h3', 'Checks needing your hardware'));
    const list = document.createElement('ul'); report.manual_checks_remaining.forEach(item => list.append(textNode('li', item))); holder.append(list);
  }
  const detail = document.createElement('details'); detail.className = 'workspace-detail';
  detail.append(textNode('summary', 'Readiness and model evidence details'), textNode('pre', JSON.stringify(data, null, 2).slice(0, 24000), 'code-output'));
  holder.append(detail); renderWatches(data.watches);
  if (data.backup?.path) element('restoreBackupPath').value = data.backup.path;
}

element('openWorkbench').onclick = () => showWorkbench(panel.hidden);
element('closeWorkbench').onclick = () => showWorkbench(false);
panel.querySelectorAll('[data-panel]').forEach(button => {
  button.onclick = () => switchPanel(button.dataset.panel);
  button.onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const names = ['Documents', 'Code', 'Maintenance'];
    let index = names.indexOf(button.dataset.panel);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
    switchPanel(names[index], true);
  };
});
element('searchDocuments').onclick = () => {
  const prompt = element('documentQuery').value.trim();
  if (!prompt) { message('Enter a phrase to search your documents.'); element('documentQuery').focus(); return; }
  action({mode: 'documents', prompt, include_history: element('documentHistory').checked});
};
element('documentQuery').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); element('searchDocuments').click(); } };
element('listDocuments').onclick = () => action({mode: 'document_list'});
element('indexDocuments').onclick = () => action({mode: 'document_index'});
element('documentFile').onchange = controls;
element('importDocument').onclick = async () => {
  const file = element('documentFile').files[0];
  if (!file || !/\.(txt|md|csv|pdf|docx)$/i.test(file.name)) { message('Choose TXT, Markdown, CSV, PDF or DOCX.'); return; }
  if (!file.size || file.size > 8 * 1024 * 1024) { message('Choose a file containing 1 byte to 8 MiB.'); return; }
  const metadata = {name: file.name, source_url: element('documentUrl').value.trim(), source_date: element('documentDate').value,
                    document_id: element('documentVersion').value};
  preparing = true; controls(); message('Reading the selected file locally…');
  try {
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('The selected file could not be read.'));
      reader.readAsDataURL(file);
    });
    preparing = false;
    await action({mode: 'document_import', content, ...metadata});
  } catch (error) { message(error.message); }
  finally { preparing = false; controls(); }
};
element('openProject').onclick = () => {
  if (preserveDraft()) return;
  if (!element('projectRoot').value.trim()) { message('Enter the full path to your project folder.'); return; }
  action(projectBody('code_open'));
};
element('projectRoot').oninput = () => { reviewed = null; element('codeDiffPanel').hidden = true; controls(); };
element('codeFile').onchange = controls;
element('readCode').onclick = () => {
  if (!preserveDraft()) action(projectBody('code_read', {file: element('codeFile').value}));
};
element('codeEditor').oninput = invalidatePreview;
element('discardCode').onclick = () => { if (opened) { element('codeEditor').value = opened.content; invalidatePreview(); message('Editor changes discarded. The project file was not modified.'); } };
element('previewCode').onclick = () => {
  if (!opened) return;
  const content = element('codeEditor').value;
  action(projectBody('code_preview', {file: opened.path, content, expected_sha256: opened.sha256}),
         {project: selectedProject, file: opened.path, content, sha256: opened.sha256});
};
element('applyCode').onclick = () => {
  if (!reviewed || !opened || reviewed.content !== element('codeEditor').value || reviewed.sha256 !== opened.sha256) { invalidatePreview(); message('Preview these exact changes before applying them.'); return; }
  action(projectBody('code_apply', {file: opened.path, content: reviewed.content, expected_sha256: opened.sha256}), {...reviewed});
};
element('undoCode').onclick = () => { if (lastEdit && !preserveDraft()) action(projectBody('code_undo', {edit_id: lastEdit.id}), {file: lastEdit.file}); };
element('runCodeTests').onclick = () => {
  if (preserveDraft()) return;
  element('codeTestOutput').textContent = 'Running the selected project check…';
  action(projectBody('code_tests', {preset: element('testPreset').value, target: element('testTarget').value.trim()}));
};
element('checkReadiness').onclick = () => action({mode: 'maintenance'});
element('benchmarkHelpers').onclick = () => action({mode: 'model_benchmark'});
function watch(enabled) {
  const provider = element('watchProvider').value, prompt = element('watchQuery').value.trim();
  if (!provider || !prompt) { message('Select the publisher and enter the exact public query.'); return; }
  action({mode: 'watch_source', provider, prompt, enabled});
}
element('enableWatch').onclick = () => watch(true); element('disableWatch').onclick = () => watch(false);
element('checkSources').onclick = () => action({mode: 'check_sources'});
element('createBackup').onclick = () => {
  const destination = element('backupDestination').value.trim();
  if (!destination) { message('Enter a private backup folder on another drive.'); return; }
  action({mode: 'backup', destination});
};
element('restoreCheck').onclick = () => {
  const backup_path = element('restoreBackupPath').value.trim(), destination = element('restoreDestination').value.trim();
  if (!backup_path || !destination) { message('Enter the backup path and a new, unused restore folder.'); return; }
  action({mode: 'restore_check', backup_path, destination});
};

window.addEventListener('prometheus-busy', event => { state = {...state, ...event.detail}; controls(); });
window.addEventListener('prometheus-partial', event => {
  if (pending?.mode === 'code_tests') element('codeTestOutput').textContent = String(event.detail?.text || '').slice(0, 32000);
});
window.addEventListener('prometheus-result', event => {
  const result = event.detail || {}, mode = result.action, data = result.details || {};
  if (!mode) return;
  const request = pending?.mode === mode ? pending : null;
  if (result.error) {
    if (mode === 'code_apply' || mode === 'code_preview') invalidatePreview();
    if (mode === 'code_tests') element('codeTestOutput').textContent += '\n\n' + result.error;
    message(result.error + ((mode === 'code_apply' || mode === 'code_preview') && dirty()
      ? ' Your draft is still in the editor. Copy it before discarding changes and reopening the current file.' : ''));
    pending = null; controls(); return;
  }
  if (mode === 'document_list') { renderDocuments(data.documents || []); message('Document versions refreshed.'); }
  else if (mode === 'documents') { renderSearch(data); message('Document search completed.'); }
  else if (mode === 'document_import') {
    message(`${data.duplicate ? 'Existing version retained' : 'Document imported'} · ${data.passage_count || 0} passages · meaning index ${data.embedding_status || 'pending'}. Refresh the document list to see all versions.`);
    jsonResult('documentResults', data); element('documentFile').value = '';
  } else if (mode === 'document_index') { message(`Meaning index: ${data.status} · ${data.indexed || 0} passages indexed · ${data.pending || 0} pending.`); }
  else if (mode === 'code_open') {
    selectedProject = data.project; element('projectRoot').value = selectedProject; element('project').value = selectedProject;
    opened = reviewed = null; element('codeEditor').value = ''; element('codeFileStatus').textContent = ''; element('codeDiffPanel').hidden = true;
    element('codeFile').replaceChildren(new Option('Choose a source file…', ''));
    for (const file of data.files || []) element('codeFile').add(new Option(`${file.path} · ${file.size} bytes`, file.path));
    element('testPreset').replaceChildren();
    for (const preset of data.test_presets || []) {
      const option = new Option(preset.label + (preset.available ? '' : ' · runtime unavailable'), preset.id);
      option.disabled = !preset.available; element('testPreset').add(option);
    }
    const available = [...element('testPreset').options].find(option => !option.disabled); if (available) element('testPreset').value = available.value;
    message(`Project opened · ${(data.files || []).length} supported files listed.`);
  } else if (mode === 'code_read') {
    element('codeEditor').value = data.content; opened = {path: data.path, sha256: data.sha256, content: element('codeEditor').value};
    element('codeFile').value = data.path; invalidatePreview(); message('File opened. Your edits are held in the editor until applied.');
  } else if (mode === 'code_preview') {
    if (request && opened && data.path === opened.path && request.project === selectedProject && request.file === opened.path && request.content === element('codeEditor').value && request.sha256 === data.before_sha256) {
      reviewed = data.changed ? request : null; element('codeDiff').textContent = data.diff || 'No file changes.';
      element('codeDiffPanel').hidden = false; element('codeDiffPanel').open = true;
      message(data.changed ? 'Review the diff, then apply these exact changes.' : 'The editor matches the file.');
    } else { reviewed = null; message('The editor changed. Preview the current text again.'); }
  } else if (mode === 'code_apply') {
    if (request && opened && opened.path === data.path) { opened.sha256 = data.sha256; opened.content = request.content; }
    if (data.edit_id) lastEdit = {id: data.edit_id, project: selectedProject, file: data.path};
    invalidatePreview(); message(data.changed ? 'Reviewed edit applied. A verified backup is available for undo.' : 'No file changes were needed.');
  } else if (mode === 'code_undo') {
    lastEdit = null; opened = reviewed = null; element('codeEditor').value = ''; element('codeFileStatus').textContent = ''; element('codeDiffPanel').hidden = true;
    message('Saved bytes restored. Open the file again to review the restored content.');
  } else if (mode === 'code_tests') {
    const status = data.cancelled ? 'Cancelled' : data.timed_out ? 'Timed out' : data.exit_code === 0 ? 'Passed' : 'Failed';
    element('codeTestOutput').textContent = `${status} · exit ${data.exit_code ?? 'none'} · ${data.duration_seconds} s\nCommand: ${JSON.stringify(data.argv)}\n\n${data.output || '(no output)'}${data.truncated ? '\n\n[Output limit reached]' : ''}`;
    message('Project check ' + status.toLowerCase() + '.');
  } else if (mode === 'maintenance') { renderReadiness(data); message('Readiness refreshed. Physical checks remain separate from device detection.'); }
  else if (mode === 'model_benchmark') { jsonResult('benchmarkResults', data); message('Available-helper checks completed. Review measured timings and individual results below.'); }
  else if (mode === 'watch_source') { message(`${data.enabled ? 'Enabled' : 'Disabled'}: ${data.provider} · ${data.query}. Refresh readiness to see all watches.`); }
  else if (mode === 'check_sources') { jsonResult('sourceWatches', data); message('Due-source check completed. Earlier versions are retained.'); }
  else if (mode === 'backup') { jsonResult('backupResults', data); if (data.path) element('restoreBackupPath').value = data.path; message(data.verified ? 'Backup created and checksums verified.' : 'Review the backup result.'); }
  else if (mode === 'restore_check') { jsonResult('restoreResults', data); message(data.verified ? 'Restore check verified in the new folder.' : 'Review the restore result.'); }
  else return;
  if (request) pending = null;
  controls();
});
window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
element('compose').addEventListener('submit', () => { if (!state.busy && element('prompt').value.trim()) showWorkbench(false); }, true);
element('prompt').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !state.busy && element('prompt').value.trim()) showWorkbench(false);
}, true);
new MutationObserver(syncProviders).observe(element('provider'), {childList: true});
element('projectRoot').value = element('project').value;
syncProviders(); controls();
})();
