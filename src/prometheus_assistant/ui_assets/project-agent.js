(function () {
'use strict';
const el = id => document.getElementById(id);
const kinds = new Set(['project_edit', 'project_task', 'idle_config']);
let state = {busy: false, online: false, closed: false}, pending = null, sending = false;
let decisionId = null, lastEdit = null, undoRequest = null, idleEnabled = false, activeMode = null;
let projectSeen = '', projectOptionsSeen = '';
const workspace = () => window.PrometheusWorkspace?.context() || {project: '', dirty: false};
function node(tag, text, className = '') {
  const item = document.createElement(tag); item.textContent = String(text ?? '');
  if (className) item.className = className;
  return item;
}
function say(text, idle = false) { el(idle ? 'idleResearchStatus' : 'projectAgentStatus').textContent = text; }
function policyHelp() {
  const delegated = el('projectAgentPolicy').value === 'delegated';
  el('projectAgentPolicyHelp').textContent = delegated
    ? 'Approve this task, selected project, checks and optional public query first. Prometheus can then choose an implementation, apply the edit and run those checks. You can stop a running task with Cancel and undo a completed edit.'
    : 'Prometheus prepares a diff and explains the reason, benefit and checks. You approve or deny that exact change.';
  el('prepareProjectTask').textContent = delegated ? 'Review task autonomy' : 'Prepare project change';
}
function controls() {
  const context = workspace(), locked = state.busy || !state.online || state.closed || sending;
  const ready = !!context.project && !context.dirty;
  el('projectAgentAutoFiles').disabled = locked || !context.project;
  el('projectAgentSpecialists').disabled = locked || !context.project;
  for (const id of ['projectAgentGoal', 'projectAgentFile', 'projectAgentFiles', 'projectAgentPolicy', 'projectAgentPreset', 'projectAgentTarget', 'projectAgentResearch']) el(id).disabled = locked || !context.project;
  if (el('projectAgentAutoFiles').checked) {
    for (const id of ['projectAgentFile', 'projectAgentFiles', 'projectAgentPolicy']) el(id).disabled = true;
  }
  el('prepareProjectTask').disabled = locked || !ready || !!pending || !!lastEdit?.id?.startsWith('recovery-');
  el('refreshProjectApproval').disabled = locked;
  el('undoProjectTask').disabled = locked || !lastEdit || !ready || lastEdit.project !== context.project || !!pending;
  el('undoProjectTask').textContent = lastEdit?.id?.startsWith('recovery-') ? 'Restore interrupted task files' : 'Undo last saved edit';
  for (const id of ['idleResearchTopic', 'idleResearchInterval', 'prepareIdleResearch', 'refreshIdleResearch']) el(id).disabled = locked || (id === 'prepareIdleResearch' && !!pending);
  el('stopIdleResearch').disabled = locked || !idleEnabled;
  el('runIdleResearch').disabled = locked || !idleEnabled || !!pending;
  for (const id of ['approveProjectTask', 'rejectProjectTask']) el(id).disabled = locked || !pending || pending.kind === 'idle_config';
  for (const id of ['approveIdleResearch', 'rejectIdleResearch']) el(id).disabled = locked || pending?.kind !== 'idle_config';
  const description = context.project
    ? 'Selected project: ' + context.project + (context.dirty ? ' · unapplied editor draft; finish or discard it before starting a task.' : '')
    : 'Open the intended project in Code. A typed folder path is not selected until Open project succeeds.';
  el('projectAgentContext').textContent = description;
  el('projectTaskContext').textContent = description;
  policyHelp();
}
function syncProject() {
  const context = workspace();
  // Retain option choices while the owner edits a folder path, but never use that draft path.
  if (context.project) {
    const files = [...el('codeFile').options].filter(option => option.value);
    const presets = [...el('testPreset').options].filter(option => option.value);
    const key = JSON.stringify([context.project, files.map(o => [o.value, o.text]), presets.map(o => [o.value, o.disabled])]);
    if (key !== projectOptionsSeen) {
      const changed = projectSeen !== context.project, file = changed ? '' : el('projectAgentFile').value;
      const preset = changed ? 'auto' : el('projectAgentPreset').value;
      el('projectAgentFile').replaceChildren(new Option('Let Prometheus choose a relevant file', ''), ...files.map(option => new Option(option.text, option.value)));
      el('projectAgentPreset').replaceChildren(new Option('Choose a relevant available check', 'auto'), ...presets.map(option => {
        const item = new Option(option.text, option.value); item.disabled = option.disabled; return item;
      }));
      if (files.some(option => option.value === file)) el('projectAgentFile').value = file;
      if (presets.some(option => option.value === preset && !option.disabled)) el('projectAgentPreset').value = preset;
      if (changed) {
        el('projectAgentAutoFiles').checked = false;
        el('projectAgentSpecialists').checked = false;
        el('projectAgentFiles').value = '';
        el('projectAgentTarget').value = ''; el('projectAgentResearch').value = '';
        el('projectAgentPolicy').value = 'ask_first';
      }
      projectSeen = context.project; projectOptionsSeen = key;
    }
  }
  controls();
}
function taskOptions() {
  const specialists = el('projectAgentSpecialists').checked ? {specialist_review: true} : {};
  if (el('projectAgentAutoFiles').checked) return {...specialists, auto_files: true, file: '', edit_policy: 'ask_first',
    preset: el('projectAgentPreset').value || 'auto', target: el('projectAgentTarget').value.trim(),
    research_query: el('projectAgentResearch').value.trim()};
  return {...specialists, file: el('projectAgentFile').value, edit_policy: el('projectAgentPolicy').value,
    ...(el('projectAgentFiles').value.trim() ? {project_files: el('projectAgentFiles').value.trim()} : {}),
    preset: el('projectAgentPreset').value || 'auto', target: el('projectAgentTarget').value.trim(),
    research_query: el('projectAgentResearch').value.trim()};
}
function currentSession() { return typeof session === 'string' && session ? {session} : {}; }
function composerContext(mode) {
  if (!['chat', 'project_edit'].includes(mode)) return {};
  const context = workspace();
  if (mode === 'project_edit') {
    if (!context.project) {
      window.PrometheusWorkspace?.show('Code');
      throw new Error('Open the intended project in Code before asking Prometheus to edit it.');
    }
    if (context.dirty) throw new Error('Your editor has unapplied changes. Apply or discard that draft before starting a project task.');
  }
  // Plain conversation may route editing intent server-side; never attach an unconfirmed folder or dirty draft.
  return {project: context.dirty ? '' : context.project, ...taskOptions()};
}
async function request(body, idle = false) {
  if (state.busy || !state.online || state.closed || sending) return false;
  sending = true; activeMode = body.mode; controls();
  say(body.mode === 'project_edit' ? 'Preparing the project task. Follow real stages in Activity.' : 'Working on ' + body.mode.replaceAll('_', ' ') + '…', idle);
  try {
    const accepted = await submitAction({...currentSession(), ...body});
    if (!accepted) { activeMode = null; say('The request was not started. Check the current connection and try again.', idle); }
    return accepted;
  } catch (error) { activeMode = null; say(error.message || 'The request could not start.', idle); return false; }
  finally { sending = false; controls(); }
}
function field(box, label, value) {
  if (value === undefined || value === null || value === '') return;
  box.append(node('p', label + ': ' + (typeof value === 'string' ? value : JSON.stringify(value, null, 2)), 'project-review-field'));
}
function renderMeasuredChecks(parent, verification) {
  const box = node('section');
  box.append(node('h4', 'Measured checks — independent of reviewer findings'));
  const checks = Array.isArray(verification?.checks) ? verification.checks : [];
  const pending = verification?.phase === 'prepared' || verification?.status === 'pending_checks';
  const failures = checks.filter(row => row.status !== 'passed' && row.status !== 'pending');
  const passed = !pending && checks.length > 0 && checks.every(row => row.status === 'passed') && verification?.status === 'passed';
  box.append(node('p', pending ? 'Checks have not run. Approval permits the displayed checks; an empty reviewer report is not a pass.' : failures.length ? 'Checks failed or remain incomplete. Reviewer findings cannot clear these results.' : passed ? 'Recorded checks passed. This is not proof that every defect was found.' : 'Check evidence is incomplete or blocked. An empty reviewer report does not clear it.'));
  for (const row of checks) box.append(node('p', `${row.preset || 'Check'} ${row.target || ''}: ${row.status || 'unknown'}`));
  parent.append(box);
}

function renderAdvisoryReview(box, result) {
  // Pure rendering also supports saved conversations: no submission, state
  // changes, approval creation or review reruns belong in this function.
  if (result === undefined || result === null) return;
  const detail = node('details', '', 'workspace-detail'); detail.open = true;
  detail.append(node('summary', 'Local advisory review'));
  const labels = {qualified_advisory: 'Advisory response recorded', unavailable: 'Unavailable',
    timed_out: 'Timed out', cancelled: 'Cancelled', invalid_input: 'Review input invalid',
    invalid_response: 'Review response invalid'};
  const state = result?.status, report = result?.report, context = report?.context;
  const valid = state === 'qualified_advisory' && result.authorized === false
    && report?.status === 'qualified_advisory' && report.authorized === false
    && typeof context?.reviewer?.model === 'string' && typeof context.reviewer.provider === 'string'
    && Array.isArray(report.findings) && report.findings.length <= 32
    && report.findings.every(row => row && typeof row.file === 'string' && Number.isInteger(row.line) && row.line > 0
      && ['low', 'normal', 'high', 'critical'].includes(row.risk) && typeof row.message === 'string'
      && typeof row.confidence === 'number' && Number.isFinite(row.confidence) && row.confidence >= 0 && row.confidence <= 1
      && row.advisory === true && row.reproducible === false);
  const label = state === 'qualified_advisory' && !valid ? labels.invalid_response
    : Object.prototype.hasOwnProperty.call(labels, state) ? labels[state] : 'Unavailable — unknown review state';
  field(detail, 'Status', label);
  if (typeof result?.reason === 'string') field(detail, 'Reason', result.reason);
  field(detail, 'Generation attempted', result?.attempted === true ? 'Yes' : result?.attempted === false ? 'No' : 'Not recorded');
  detail.append(node('p', 'Advisory only. Deterministic checks and owner approval remain separate.', 'fine'));
  box.append(detail);
  if (!valid) {
    if (state === 'qualified_advisory') detail.append(node('p', 'Saved advisory report is unavailable or invalid. No qualified findings can be displayed.', 'fine'));
    return;
  }
  field(detail, 'Reviewer', context.reviewer.model);
  field(detail, 'Provider', context.reviewer.provider);
  const relationship = context.artifact_relationship === 'same_artifact'
    ? 'Same model artifact as the author; this is not a distinct reviewer artifact.'
    : context.artifact_relationship === 'different_artifact'
      ? 'Different model artifact from the author; independent reasoning is not established.'
      : 'Relationship to the author is unknown.';
  detail.append(node('p', relationship, 'fine'));
  if (!report.findings.length) detail.append(node('p', 'No findings were reported. This does not mean the checks passed or the change is correct.'));
  for (const finding of report.findings) {
    const card = node('article', '', 'workspace-card');
    card.append(node('strong', finding.file + ':' + finding.line + ' · ' + finding.risk + ' risk'));
    field(card, 'Category', finding.category);
    field(card, 'Model confidence', Math.round(finding.confidence * 100) + '% (unverified)');
    card.append(node('p', finding.message));
    field(card, 'Reproduction', finding.reproduction_claimed === true
      ? 'Claimed by the model; unverified.' : 'No verified reproduction evidence.');
    detail.append(card);
  }
  const identity = node('details', '', 'workspace-detail');
  identity.append(node('summary', 'Review identity and receipt'));
  field(identity, 'Reviewer artifact SHA256', context.reviewer.artifact_sha256 || 'Unknown');
  field(identity, 'Author artifact SHA256', context.author?.artifact_sha256 || 'Unknown');
  field(identity, 'Candidate SHA256', context.candidate_sha256);
  field(identity, 'Request SHA256', context.request_sha256);
  field(identity, 'Prompt version', context.prompt_version);
  field(identity, 'Prompt SHA256', context.prompt_sha256);
  field(identity, 'Record hash (integrity only)', report.receipt_sha256);
  identity.append(node('p', 'Recorded provenance does not independently prove model execution or correctness.', 'fine'));
  detail.append(identity);
}
function renderSpecialistReview(box, result) {
  // History and live results share this pure display; it never queues a stage
  // or treats a saved report as authority. A present team replaces legacy advice.
  if (result === undefined || result === null) return false;
  const detail = node('details', '', 'workspace-detail'); detail.open = true;
  detail.append(node('summary', 'Specialist review'));
  detail.append(node('p', 'Sequential local reviews: one inference at a time, with fresh resource checks before each stage. Recorded resources describe that stage, not current availability.', 'fine'));
  detail.append(node('p', 'Stage completion is not verification. Deterministic checks and owner approval remain separate.', 'fine'));
  box.append(detail);
  const roles = {correctness: 'Correctness', test_coverage: 'Test coverage'};
  const labels = {completed: 'Completed — advisory only', deferred: 'Deferred', unavailable: 'Unavailable',
    cancelled: 'Cancelled', not_run: 'Not run'};
  const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
  const valid = result?.schema_version === 1 && result.pipeline_version === 'project-specialists-v1'
    && result.authorized === false && result.approval_required === true && result.max_model_concurrency === 1
    && digest(result.candidate_sha256) && digest(result.receipt_sha256)
    && ['completed', 'incomplete', 'cancelled'].includes(result.status)
    && Array.isArray(result.stages) && result.stages.length === 2
    && result.stages.every(stage => stage && Object.prototype.hasOwnProperty.call(roles, stage.role)
      && Object.prototype.hasOwnProperty.call(labels, stage.status))
    && new Set(result.stages.map(stage => stage.role)).size === 2
    && (result.status !== 'completed' || result.stages.every(stage => stage.status === 'completed'));
  if (!valid) {
    detail.append(node('p', 'Saved specialist review is unavailable or invalid. No completed review can be established from this record.', 'fine'));
    return true;
  }
  field(detail, 'Status', {completed: 'Completed — advisory only', incomplete: 'Incomplete — review stages remain unfinished',
    cancelled: 'Cancelled — review stages were stopped'}[result.status]);
  const artifacts = result.stages.map(stage => stage.advice?.report?.context?.reviewer?.artifact_sha256);
  detail.append(node('p', artifacts.every(digest) && artifacts[0] === artifacts[1]
    ? 'Both review roles used the same model artifact; separate roles do not establish independent reasoning.'
    : 'Distinct review roles do not establish independent reasoning. Model provenance is shown for each available report.', 'fine'));
  for (const stage of result.stages) {
    const card = node('section', '', 'workspace-card');
    card.append(node('h4', roles[stage.role])); field(card, 'Stage', labels[stage.status]);
    const resources = stage.resources;
    if (resources && typeof resources === 'object' && !Array.isArray(resources)) {
      field(card, 'Resource decision', typeof resources.decision === 'string' ? resources.decision.slice(0, 200) : 'Unknown');
      const reasons = Array.isArray(resources.reasons) ? resources.reasons : [];
      for (const reason of reasons.slice(0, 8)) if (typeof reason === 'string') field(card, 'Resource reason', reason.slice(0, 1000));
      const observations = resources.observations || {};
      for (const [key, label, unit] of [['available_ram_gib', 'Available RAM', ' GiB'], ['cpu_percent', 'CPU use', '%'],
        ['free_disk_gib', 'Free disk', ' GiB']]) {
        const value = observations[key];
        field(card, label, typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value + unit : 'Unknown');
      }
      field(card, 'External power', observations.plugged_in === true ? 'Connected' : observations.plugged_in === false ? 'Not connected' : 'Unknown');
    } else card.append(node('p', 'Resource assessment was not recorded for this stage.', 'fine'));
    if (stage.advice) renderAdvisoryReview(card, stage.advice);
    else card.append(node('p', 'No advisory result was recorded for this stage.', 'fine'));
    detail.append(card);
  }
  const identity = node('details', '', 'workspace-detail');
  identity.append(node('summary', 'Specialist review identity'));
  field(identity, 'Candidate SHA256', result.candidate_sha256);
  field(identity, 'Pipeline version', result.pipeline_version);
  field(identity, 'Record hash (integrity only)', result.receipt_sha256);
  detail.append(identity);
  return true;
}
function review(approval, show = false) {
  pending = kinds.has(approval?.kind) ? approval : null;
  for (const [box, buttons] of [['projectAgentReview', 'projectAgentDecisions'], ['idleResearchReview', 'idleResearchDecisions']]) {
    el(box).replaceChildren(); el(box).hidden = true; el(buttons).hidden = true;
  }
  if (pending) {
    const idle = pending.kind === 'idle_config', data = pending.review || {};
    const box = el(idle ? 'idleResearchReview' : 'projectAgentReview'); box.hidden = false;
    el(idle ? 'idleResearchDecisions' : 'projectAgentDecisions').hidden = false;
    box.append(node('h3', data.action || 'Review proposed access'));
    for (const [key, label] of [['reason', 'Why'], ['benefit', 'Benefit'], ['effects', 'Effects'], ['project', 'Project'], ['target', 'Target'], ['check_reason', 'Why these checks'], ['duration', 'Duration']]) field(box, label, data[key]);
    if (data.diff) {
      const diff = node('details', '', 'workspace-detail'); diff.open = true;
      diff.append(node('summary', 'Exact proposed change'), node('pre', data.diff, 'code-output')); box.append(diff);
    }
    for (const [key, label] of [['checks', 'Checks in this review'], ['resources', 'Resources and network access'], ['resource_assessment', 'Resource estimate and admission'], ['verification', 'Verification evidence — proposed checks have not run']]) {
      if (data[key] !== undefined) {
        const detail = node('details', '', 'workspace-detail'); detail.open = true;
        detail.append(node('summary', label), node('pre', typeof data[key] === 'string' ? data[key] : JSON.stringify(data[key], null, 2), 'code-output')); box.append(detail);
      }
    }
    renderMeasuredChecks(box, data.verification);
    if (!renderSpecialistReview(box, data.specialist_review)) renderAdvisoryReview(box, data.advisory_review);
    box.append(node('p', 'Approve or deny this review below, or say “yes, approve” or “deny” in its conversation. Approval applies to this exact review; changes to these form fields require a new proposal.', 'fine'));
    if (pending.expires_utc) field(box, 'Review expires', pending.expires_utc);
    if (show) window.PrometheusWorkspace?.show(idle ? 'Maintenance' : 'Code');
    say('Review the reason, benefit, changes and access before deciding.', idle);
  }
  controls();
}
async function refreshApproval() {
  if (state.busy || !state.online || sending) return;
  sending = true; controls();
  try {
    const data = await api('/api/approvals/pending');
    if (state.closed || !state.online) return;
    review(data.approval, true);
    if (!pending) say(data.approval ? 'Another proposal is awaiting review. Check Integrations or Improve Prometheus in Maintenance.' : 'No pending project or research approval in this owner session.');
  } catch (error) { say(error.message); }
  finally { sending = false; controls(); }
}
async function decide(decision) {
  if (!pending) return;
  const selected = pending; decisionId = selected.id;
  const accepted = await request({mode: 'approval_decision', approval_decision: decision,
    approval_id: selected.id, approval_fingerprint: selected.fingerprint, session: selected.session}, selected.kind === 'idle_config');
  if (!accepted) decisionId = null;
}
function renderProject(data, reply) {
  const rolledBack = !!(data.rolled_back || data.rollback?.undone);
  const canUndo = data.undo?.available !== false && data.changed && !rolledBack;
  const box = el('projectAgentResult'); box.replaceChildren();
  box.append(node('h3', 'Latest project task result'), node('p', reply || 'Review the recorded task result.'));
  for (const [key, label] of [['state', 'State'], ['project', 'Project'], ['path', 'File'], ['edit_id', 'Undo record']]) field(box, label, data[key]);
  if (data.changed !== undefined) field(box, 'File changed', data.changed ? 'Yes' : 'No');
  if (rolledBack) field(box, 'Recovery', 'Original file restored after unsuccessful checks or cancellation.');
  if (data.error) field(box, 'Task needs attention', data.error);
  if (data.rollback?.error) field(box, 'Recovery needs attention', data.rollback.error);
  if (data.undo?.reason) field(box, 'Undo', data.undo.reason);
  if (data.recovery?.length) field(box, 'Recovery by file', data.recovery);
  renderMeasuredChecks(box, data.verification);
  if (data.verification) field(box, 'Verification evidence', data.verification);
  if (data.measured_review) {
    box.append(node('p', 'Post-check review uses recorded process outcomes. Model locations are unverified; failed checks remain failures.', 'fine'));
    field(box, 'Post-check evidence review', data.measured_review);
  }
  if (!renderSpecialistReview(box, data.specialist_review)) renderAdvisoryReview(box, data.advisory_review);
  if (data.resource_assessment) field(box, 'Resource admission at execution', data.resource_assessment);
  if (data.resource_measurements) field(box, 'Measured check resources and history', data.resource_measurements);
  if (data.checks !== undefined) {
    const detail = node('details', '', 'workspace-detail'); detail.open = true;
    detail.append(node('summary', 'Recorded check results'), node('pre', JSON.stringify(data.checks, null, 2), 'code-output')); box.append(detail);
  }
  if (data.project && (data.changed || rolledBack)) window.PrometheusWorkspace?.invalidate(data.project, data.edits ? undefined : data.path);
  if (data.project && data.edit_id && canUndo) lastEdit = {project: data.project, id: data.edit_id, path: data.path};
  if (rolledBack || data.undo?.available === false) lastEdit = null;
  say(reply || 'Project task finished. Review the result and check output.'); controls();
}
function restoreUndo(data) {
  // The server only returns an edit whose saved after-hash matches the current file.
  // Never recover a different tab's selection or a record from another project.
  if (!data.project || workspace().project !== data.project) return;
  const saved = data.latest_edit;
  lastEdit = saved && saved.project === data.project && typeof saved.edit_id === 'string' && saved.edit_id
    && typeof saved.path === 'string' && saved.path ? {project: saved.project, id: saved.edit_id, path: saved.path} : null;
  el('projectAgentResult').replaceChildren();
  if (lastEdit) {
    el('projectAgentResult').append(node('p', 'A saved edit is available for undo in this project.'));
    field(el('projectAgentResult'), 'File', lastEdit.path); field(el('projectAgentResult'), 'Undo record', lastEdit.id);
    say((lastEdit.id.startsWith('recovery-') ? 'An interrupted task needs recovery for ' : 'Saved undo is available for ') + lastEdit.path + '. Restore checks every file first and preserves newer work.');
  } else say('Project ready. No matching saved edit is available for undo.');
  controls();
}
function sourceLink(source) {
  const location = typeof source === 'string' ? source : source.url || source.source_url || '';
  const label = typeof source === 'string' ? source : source.title || location;
  const line = node('p', label, 'fine source-location');
  try {
    const url = new URL(location);
    if (['https:', 'http:'].includes(url.protocol) && !url.username && !url.password) {
      const link = node('a', label || url.hostname, 'source-link');
      link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      line.replaceChildren(link);
    }
  } catch { /* Keep unknown references as literal text. */ }
  return line;
}
function renderIdle(data) {
  const config = data.settings || data.config || data;
  if (typeof config.enabled === 'boolean') idleEnabled = config.enabled;
  const topic = config.query || config.topic || '';
  say('Idle research ' + (idleEnabled ? 'enabled' : 'stopped') + (topic ? ' · ' + topic : '')
    + (config.interval_minutes ? ' · at least ' + config.interval_minutes + ' minutes between checks' : '')
    + (config.next_due_utc ? ' · next due ' + config.next_due_utc : data.next_run_at ? ' · next due ' + new Date(data.next_run_at * 1000).toLocaleString() : '')
    + (data.running ? ' · running now' : data.due ? ' · due when idle' : ''), true);
  const latest = data.result || data.last_result || (data.state ? data : null);
  const rows = Array.isArray(data.history) ? [...data.history].reverse().filter(row => !latest || row.finished_at !== latest.finished_at || row.topic !== latest.topic) : [];
  if (latest) rows.unshift(latest);
  const box = el('idleResearchFindings'); box.replaceChildren();
  if (!rows.length) box.append(node('p', 'No research findings in this response. Refresh after an approved check runs.', 'fine'));
  for (const row of rows) {
    const card = node('article', '', 'workspace-card');
    card.append(node('strong', row.query || row.topic || 'Public research'));
    field(card, 'Checked', row.checked_utc || row.created_utc || row.timestamp || (row.finished_at ? new Date(row.finished_at * 1000).toLocaleString() : ''));
    field(card, 'State', row.state);
    field(card, 'Source service', row.provider);
    if (row.summary || row.reply) card.append(node('p', row.summary || row.reply));
    if (row.error) card.append(node('p', row.error, 'fine'));
    if (row.reason) card.append(node('p', row.reason, 'fine'));
    if (row.summary_error) card.append(node('p', 'Summary unavailable: ' + row.summary_error + ' Source findings remain available.', 'fine'));
    const sources = row.sources || row.results || [];
    for (const source of Array.isArray(sources) ? sources : []) {
      card.append(sourceLink(source));
      if (source.excerpt || source.snippet || source.description) card.append(node('p', source.excerpt || source.snippet || source.description, 'fine'));
    }
    if (row.source_count !== undefined && !sources.length) field(card, 'Sources found', row.source_count);
    card.append(node('p', 'Saved public-source findings. Treat source content as evidence to review.', 'fine'));
    box.append(card);
  }
  controls();
}
el('projectAgentPolicy').onchange = controls;
el('projectAgentAutoFiles').onchange = () => {
  if (el('projectAgentAutoFiles').checked) {
    el('projectAgentPolicy').value = 'ask_first';
    el('projectAgentFile').value = ''; el('projectAgentFiles').value = '';
  }
  controls();
};
el('projectTaskOptions').onclick = () => window.PrometheusWorkspace?.show('Code');
el('prepareProjectTask').onclick = async () => {
  const prompt = el('projectAgentGoal').value.trim();
  if (!prompt) { say('Describe the behavior or file you want to change.'); el('projectAgentGoal').focus(); return; }
  try { await request({mode: 'project_edit', prompt, ...composerContext('project_edit')}); }
  catch (error) { say(error.message); }
};
el('refreshProjectApproval').onclick = refreshApproval;
el('approveProjectTask').onclick = () => decide('approve'); el('rejectProjectTask').onclick = () => decide('reject');
el('approveIdleResearch').onclick = () => decide('approve'); el('rejectIdleResearch').onclick = () => decide('reject');
el('undoProjectTask').onclick = async () => {
  const context = workspace();
  if (!lastEdit || context.project !== lastEdit.project || context.dirty) return;
  undoRequest = {...lastEdit};
  if (!await request({mode: 'code_undo', project: lastEdit.project, edit_id: lastEdit.id})) undoRequest = null;
};
el('prepareIdleResearch').onclick = () => {
  const prompt = el('idleResearchTopic').value.trim(), interval_minutes = Number(el('idleResearchInterval').value);
  if (!prompt) { say('Enter the exact public topic to research.', true); el('idleResearchTopic').focus(); return; }
  if (!Number.isInteger(interval_minutes) || interval_minutes < 15 || interval_minutes > 1440) { say('Choose a whole number from 15 to 1440 minutes between checks.', true); return; }
  return request({mode: 'idle_config', prompt, enabled: true, interval_minutes}, true);
};
el('stopIdleResearch').onclick = () => request({mode: 'idle_config', enabled: false}, true);
el('refreshIdleResearch').onclick = () => request({mode: 'idle_status'}, true);
el('runIdleResearch').onclick = () => { if (idleEnabled) return request({mode: 'idle_run'}, true); };
window.addEventListener('prometheus-busy', event => { state = {...state, ...event.detail}; controls(); });
window.addEventListener('prometheus-partial', event => {
  if (activeMode === 'project_edit' || activeMode === 'approval_decision' && pending?.kind !== 'idle_config') say(String(event.detail?.text || 'Working…').slice(-1200));
});
window.addEventListener('prometheus-result', event => {
  if (state.closed) return;
  const result = event.detail || {}, data = result.details || {};
  // Results observed in another tab cannot replace this tab's proposal or its draft.
  if (result.origin === 'observed') {
    if (data.project_edit?.project && (data.project_edit.changed || data.project_edit.rolled_back || data.project_edit.rollback?.undone)) window.PrometheusWorkspace?.invalidate(data.project_edit.project, data.project_edit.edits ? undefined : data.project_edit.path);
    return;
  }
  const resolved = data.approval_resolved;
  if (resolved && (pending?.id === resolved || decisionId === resolved)) {
    const idle = pending?.kind === 'idle_config'; review(null); decisionId = null; say(result.reply || 'Review resolved.', idle);
  }
  if (data.approval) { review(data.approval, kinds.has(data.approval.kind)); activeMode = null; }
  if (result.action === 'code_open' && !result.error) restoreUndo(data);
  if (data.project_edit) renderProject(data.project_edit, result.reply);
  if (data.idle_research) renderIdle(data.idle_research);
  if (result.action === 'code_undo' && undoRequest) {
    if (result.error) say('Undo needs attention: ' + result.error + ' The recorded edit remains available.');
    else {
      window.PrometheusWorkspace?.invalidate(undoRequest.project, undoRequest.path);
      lastEdit = null; say('Original file bytes restored. Open the file again to review them.');
      el('projectAgentResult').replaceChildren(node('p', 'This task’s edit was undone. Original file bytes were restored.'));
    }
    undoRequest = null;
  } else if (result.error && (['project_edit', 'idle_config', 'idle_status', 'idle_run'].includes(result.action) || result.action === 'approval_decision' && decisionId)) {
    const idle = result.action.startsWith('idle_') || pending?.kind === 'idle_config';
    say(result.error + (decisionId ? ' Refresh pending review before trying again.' : ''), idle);
    if (decisionId) { decisionId = null; review(null); }
  }
  if (result.action === activeMode || resolved || data.project_edit || data.idle_research) activeMode = null;
  syncProject();
});
window.addEventListener('prometheus-auth-lost', () => {
  state = {busy: false, online: false, closed: true}; review(null); lastEdit = null; idleEnabled = false;
  el('projectAgentResult').replaceChildren(); el('idleResearchFindings').replaceChildren(); controls();
});
window.PrometheusProjectAgent = {composerContext, workspaceChanged: syncProject, renderAdvisoryReview, renderSpecialistReview};
syncProject();
})();
