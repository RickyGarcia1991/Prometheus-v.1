'use strict';
// Exercise the real workspace + task UI contracts without network or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {setup} = require('./test-workspace.cjs');
const assets = path.join(__dirname, '../../src/prometheus_assistant/ui_assets');
const source = fs.readFileSync(path.join(assets, 'project-agent.js'), 'utf8');
const markup = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
const PROJECT = 'C:\\Projects\\demo';
const approval = (kind = 'project_edit') => ({id: 'review-1', fingerprint: 'exact-hash', session: 'original-conversation', kind,
  review: {action: 'Change greeting', reason: 'Handle empty names', benefit: 'Consistent output', effects: 'One selected file changes',
    project: PROJECT, target: 'greeting.py', diff: '-old\n+new', checks: [{preset: 'python_syntax', target: 'greeting.py'}],
    resources: {project: PROJECT, research_query: ''},
    resource_assessment: {decision: 'run_local', confidence: 'low'},
    verification: {status: 'pending_checks'}, duration: 'This task only'}});
const advisory = (findings = []) => ({status: 'qualified_advisory', reason_code: 'qualified',
  reason: 'Recorded local advice.', attempted: true, authorized: false,
  report: {status: 'qualified_advisory', authorized: false, findings,
    receipt_sha256: 'e'.repeat(64), context: {
      reviewer: {provider: 'ollama-local', model: 'local-checker', artifact_sha256: 'b'.repeat(64)},
      author: {provider: 'ollama-local', model: 'writer', artifact_sha256: 'a'.repeat(64)},
      artifact_relationship: 'different_artifact', candidate_sha256: 'c'.repeat(64),
      request_sha256: 'd'.repeat(64), prompt_version: 'local-advisory-v1', prompt_sha256: 'f'.repeat(64)}}});
function descendants(item) { return [item, ...item.children.flatMap(descendants)]; }
function text(item) { return [item.textContent, ...item.children.map(text)].join('\n'); }
function specialistReview(status = 'completed') {
  return {schema_version: 1, pipeline_version: 'project-specialists-v1', candidate_sha256: 'c'.repeat(64),
    files: ['greeting.py'], status, max_model_concurrency: 1, authorized: false, approval_required: true,
    receipt_sha256: '9'.repeat(64), stages: ['correctness', 'test_coverage'].map(role => ({
      role, label: role === 'correctness' ? 'Correctness' : 'Test coverage', status: 'completed',
      advice: advisory(), resources: {decision: 'run_local', reasons: [],
        observations: {available_ram_gib: 3.5, cpu_percent: 12, plugged_in: true}}}))};
}
function fixture() {
  const f = setup(), e = f.elements;
  for (const match of source.matchAll(/el\('([^']+)'\)/g)) assert(e[match[1]], 'missing HTML id: ' + match[1]);
  f.context.session = 'current-conversation';
  f.pendingApproval = null;
  f.context.api = async url => { assert.equal(url, '/api/approvals/pending'); return {approval: f.pendingApproval}; };
  vm.runInNewContext(source, f.context, {filename: 'project-agent.js'});
  f.busy = (busy = false) => f.emit('prometheus-busy', {busy, online: true, closed: false});
  f.input = (id, value) => { e[id].value = value; e[id].dispatch('input'); };
  f.last = () => f.submitted.at(-1);
  f.contextFor = mode => f.context.window.PrometheusProjectAgent.composerContext(mode);
  f.open = async (project = PROJECT) => {
    f.input('projectRoot', project); await e.openProject.click();
    f.result('code_open', {project, files: [{path: 'greeting.py', size: 30}, {path: 'README.md', size: 10}],
      test_presets: [{id: 'python_syntax', label: 'Python syntax', available: true}, {id: 'pytest', label: 'Pytest', available: false}]}, {origin: 'local'});
  };
  f.read = async () => {
    e.codeFile.value = 'greeting.py'; e.codeFile.dispatch('change'); await e.readCode.click();
    f.result('code_read', {path: 'greeting.py', content: 'print("old")\n', sha256: 'original'}, {origin: 'local'});
  };
  return f;
}
(async () => {
  const calibration = fixture(); calibration.busy(); await calibration.open();
  for (const applied of [false, true]) {
    const proposed = approval();
    proposed.review.resource_assessment.prediction_calibration = {applied};
    calibration.result('project_edit', {approval: proposed}, {origin: 'local'});
    assert.match(text(calibration.elements.projectAgentReview), applied
      ? /correction passed separate held-out checks/ : /using the conservative baseline/);
  }
  const team = fixture(); team.busy(); await team.open();
  const checkbox = team.elements.projectAgentSpecialists;
  assert(checkbox, 'optional specialist control exists');
  assert.equal(checkbox.checked, false, 'specialist review starts off');
  assert(!checkbox.disabled, 'opened project permits opt-in');
  assert(!('specialist_review' in team.contextFor('project_edit')), 'off preserves legacy request shape');
  checkbox.checked = true;
  assert.equal(team.contextFor('project_edit').specialist_review, true);
  assert.equal(team.contextFor('chat').specialist_review, true, 'conversational project requests retain the choice');
  team.elements.projectAgentGoal.value = 'Handle empty input';
  await team.elements.prepareProjectTask.click();
  assert.equal(team.last().specialist_review, true, 'Code submits opt-in');
  team.elements.projectAgentAutoFiles.checked = true;
  assert.equal(team.contextFor('project_edit').specialist_review, true, 'automatic file selection retains opt-in');
  team.busy(true); assert(checkbox.disabled, 'active task locks the choice'); team.busy();
  await team.open('C:\\Projects\\other');
  assert.equal(checkbox.checked, false, 'new selected project resets task-specific choice');
  assert(!('specialist_review' in team.contextFor('project_edit')));

  const report = specialistReview();
  const reviewedTeam = approval(); reviewedTeam.review.specialist_review = report;
  reviewedTeam.review.advisory_review = advisory([{file: 'legacy.py', line: 1, risk: 'low', confidence: .1,
    message: 'Do not duplicate legacy advice', advisory: true, reproducible: false}]);
  const beforeTeamRender = team.submitted.length;
  team.result('project_edit', {approval: reviewedTeam}, {origin: 'local'});
  let teamText = text(team.elements.projectAgentReview);
  assert.match(teamText, /Specialist review/);
  assert.match(teamText, /Correctness/); assert.match(teamText, /Test coverage/);
  assert.match(teamText, /one inference at a time/i);
  assert.match(teamText, /same model artifact/i);
  assert.match(teamText, /do not establish independent reasoning/i);
  assert.match(teamText, /owner approval remain separate/i);
  assert.match(teamText, /Available RAM.*3.5/);
  assert.doesNotMatch(teamText, /Do not duplicate legacy advice/);
  assert.equal(descendants(team.elements.projectAgentReview).filter(n => n.tagName === 'SUMMARY' && n.textContent === 'Local advisory review').length, 2);
  assert.equal(team.submitted.length, beforeTeamRender, 'review display cannot dispatch a stage');

  const partial = specialistReview('incomplete');
  partial.stages[1] = {role: 'test_coverage', label: '<script>label</script>', status: 'deferred', advice: null,
    resources: {decision: 'defer', reasons: ['<img src=x onerror=alert(1)> low memory'], observations: {available_ram_gib: null}}};
  team.result('approval_decision', {approval_resolved: 'review-1', project_edit: {
    project: PROJECT, changed: false, specialist_review: partial, advisory_review: reviewedTeam.review.advisory_review}}, {origin: 'local'});
  teamText = text(team.elements.projectAgentResult);
  assert.match(teamText, /Incomplete/); assert.match(teamText, /Deferred/);
  assert.match(teamText, /<img src=x onerror=alert\(1\)> low memory/);
  assert.match(teamText, /not verification/i);
  assert.doesNotMatch(teamText, /Do not duplicate legacy advice/);
  assert(!descendants(team.elements.projectAgentResult).some(n => ['IMG', 'SCRIPT', 'A'].includes(n.tagName)));
  const cancelled = specialistReview('cancelled');
  cancelled.stages = [{role: 'correctness', label: 'Correctness', status: 'cancelled', advice: null, resources: null},
    {role: 'test_coverage', label: 'Test coverage', status: 'not_run', advice: null, resources: null}];
  const savedTeamBox = team.context.document.createElement('article'), serializedTeam = JSON.stringify(cancelled);
  const beforeTeamHistory = team.submitted.length;
  assert.equal(team.context.window.PrometheusProjectAgent.renderSpecialistReview(savedTeamBox, cancelled), true);
  assert.match(text(savedTeamBox), /Cancelled/); assert.match(text(savedTeamBox), /Not run/);
  assert.match(text(savedTeamBox), /not recorded/i);
  assert.equal(JSON.stringify(cancelled), serializedTeam, 'history preserves the original team report');
  assert.equal(team.submitted.length, beforeTeamHistory);
  assert(!descendants(savedTeamBox).some(n => ['BUTTON', 'INPUT', 'A'].includes(n.tagName)));
  for (const invalid of [{...report, authorized: true}, {...report, max_model_concurrency: 2},
    {...report, stages: [report.stages[0], report.stages[0]]}, {...partial, status: 'completed'}]) {
    const box = team.context.document.createElement('article');
    team.context.window.PrometheusProjectAgent.renderSpecialistReview(box, invalid);
    assert.match(text(box), /unavailable or invalid/i);
    assert.doesNotMatch(text(box), /Reviewer: local-checker/);
  }
  assert.equal(team.context.window.PrometheusProjectAgent.renderSpecialistReview(savedTeamBox, undefined), false);

  const reviewed = fixture(); reviewed.busy(); await reviewed.open();
  const finding = {file: 'greeting.py', line: 2, risk: 'high', confidence: .8,
    category: 'correctness', message: '<img src=x onerror=alert(1)> potential defect',
    reproduction_claimed: true, reproducible: false, advisory: true};
  const assessment = advisory([finding, {...finding, risk: 'low', confidence: .3,
    message: 'Disagreement: this branch may be correct.', reproduction_claimed: false}]);
  const reviewedApproval = approval(); reviewedApproval.review.advisory_review = assessment;
  const requestsBeforeReview = reviewed.submitted.length;
  reviewed.result('project_edit', {approval: reviewedApproval}, {origin: 'local'});
  const reviewText = text(reviewed.elements.projectAgentReview);
  assert.match(reviewText, /Local advisory review/);
  assert.match(reviewText, /Reviewer: local-checker/);
  assert.match(reviewText, /Different model artifact/);
  assert.match(reviewText, /independent reasoning is not established/);
  assert.match(reviewText, /greeting\.py:2/);
  assert.match(reviewText, /high risk/);
  assert.match(reviewText, /80%/);
  assert.match(reviewText, /Claimed by the model; unverified/);
  assert.match(reviewText, /Disagreement: this branch may be correct/);
  assert.match(reviewText, /<img src=x onerror=alert\(1\)>/);
  assert(!descendants(reviewed.elements.projectAgentReview).some(n => ['IMG', 'SCRIPT'].includes(n.tagName)), 'advisory markup is literal text');
  assert.equal(reviewed.submitted.length, requestsBeforeReview, 'rendering never submits an advisory or decision');
  assert(!reviewed.elements.approveProjectTask.disabled && !reviewed.elements.rejectProjectTask.disabled, 'owner decision controls are unchanged');
  assert.match(reviewText, new RegExp('b'.repeat(64)), 'full artifact digest remains inspectable');
  reviewed.result('approval_decision', {approval_resolved: 'review-1', project_edit: {
    project: PROJECT, path: 'greeting.py', changed: false, advisory_review: assessment}}, {origin: 'local'});
  assert.match(text(reviewed.elements.projectAgentResult), /local-checker/);
  assert.match(text(reviewed.elements.projectAgentResult), /Disagreement/);

  const savedBox = reviewed.context.document.createElement('article');
  const originalAssessment = JSON.stringify(assessment), beforeHistory = reviewed.submitted.length;
  reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(savedBox, assessment);
  assert.match(text(savedBox), /potential defect/);
  assert.equal(JSON.stringify(assessment), originalAssessment, 'saved report is rendered without mutation');
  assert.equal(reviewed.submitted.length, beforeHistory, 'saved report does not trigger another review');
  assert(!descendants(savedBox).some(n => ['BUTTON', 'INPUT', 'A'].includes(n.tagName)), 'saved advisory text adds no action authority');

  const emptyBox = reviewed.context.document.createElement('article');
  reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(emptyBox, advisory());
  assert.match(text(emptyBox), /No findings were reported/);
  assert.match(text(emptyBox), /does not mean the checks passed/);
  for (const [status, label] of [['unavailable', 'Unavailable'], ['timed_out', 'Timed out'],
    ['cancelled', 'Cancelled'], ['invalid_input', 'Review input invalid'], ['invalid_response', 'Review response invalid']]) {
    const unavailable = {status, reason: '<script>why</script>', attempted: false, authorized: false, report: null};
    const prepared = approval(); prepared.review.advisory_review = unavailable;
    reviewed.result('project_edit', {approval: prepared}, {origin: 'local'});
    assert.match(text(reviewed.elements.projectAgentReview), new RegExp(label));
    assert.match(text(reviewed.elements.projectAgentReview), /<script>why<\/script>/);
    assert(!descendants(reviewed.elements.projectAgentReview).some(n => n.tagName === 'SCRIPT'));
    reviewed.result('approval_decision', {approval_resolved: 'review-1', project_edit: {
      project: PROJECT, changed: false, advisory_review: unavailable}}, {origin: 'local'});
    assert.match(text(reviewed.elements.projectAgentResult), new RegExp(label));
  }
  for (const [relationship, message] of [['same_artifact', /Same model artifact/], ['unestablished', /unknown/]]) {
    const original = advisory(); original.report.context.artifact_relationship = relationship;
    const box = reviewed.context.document.createElement('article');
    reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(box, original);
    assert.match(text(box), message);
  }
  const malformedBox = reviewed.context.document.createElement('article');
  reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(malformedBox, {...advisory(), report: null});
  assert.match(text(malformedBox), /Saved advisory report is unavailable or invalid/);
  for (const change of ['approval', 'reproduction', 'missing-findings']) {
    const forged = advisory([finding]);
    if (change === 'approval') forged.authorized = true;
    else if (change === 'reproduction') forged.report.findings[0] = {...finding, reproducible: true};
    else forged.report.findings = null;
    const box = reviewed.context.document.createElement('article');
    reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(box, forged);
    assert.match(text(box), /Review response invalid/);
    assert.doesNotMatch(text(box), /potential defect/, 'invalid reports do not present qualified findings');
  }
  const untrustedIdentity = advisory();
  untrustedIdentity.report.context.reviewer.model = '<script>model</script>';
  untrustedIdentity.report.context.reviewer.artifact_sha256 = '<img src=x onerror=alert(1)>';
  const identityBox = reviewed.context.document.createElement('article');
  reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(identityBox, untrustedIdentity);
  assert.match(text(identityBox), /<script>model<\/script>/);
  assert.match(text(identityBox), /<img src=x onerror=alert\(1\)>/);
  assert(!descendants(identityBox).some(n => ['IMG', 'SCRIPT', 'A'].includes(n.tagName)));
  const legacyBox = reviewed.context.document.createElement('article');
  reviewed.context.window.PrometheusProjectAgent.renderAdvisoryReview(legacyBox, undefined);
  assert.equal(legacyBox.children.length, 0, 'legacy results without an advisory report remain compatible');

  const automatic = fixture(); automatic.busy(); await automatic.open();
  automatic.elements.projectAgentAutoFiles.checked = true;
  automatic.elements.projectAgentAutoFiles.onchange();
  assert(automatic.elements.projectAgentFile.disabled);
  automatic.elements.projectAgentGoal.value = 'Fix greeting and tests';
  await automatic.elements.prepareProjectTask.click();
  assert.equal(automatic.last().auto_files, true);
  assert.equal(automatic.last().edit_policy, 'ask_first');
  automatic.result('code_open', {project: PROJECT, files: [], test_presets: [],
    latest_edit: {project: PROJECT, path: 'greeting.py, README.md', edit_id: 'recovery-123', interrupted: true}}, {origin: 'local'});
  assert.match(automatic.elements.projectAgentStatus.textContent, /interrupted/);
  assert.equal(automatic.elements.undoProjectTask.textContent, 'Restore interrupted task files');
  assert(automatic.elements.prepareProjectTask.disabled);
  await automatic.elements.undoProjectTask.click();
  assert.equal(automatic.last().edit_id, 'recovery-123');
  const group = fixture(); group.busy(); await group.open(); await group.read();
  group.elements.projectAgentGoal.value = 'Update both files';
  group.elements.projectAgentFiles.value = 'greeting.py\nREADME.md';
  await group.elements.prepareProjectTask.click();
  assert.equal(group.last().project_files, 'greeting.py\nREADME.md');
  const combined = approval(); combined.review.diff = '--- a/greeting.py\n+new\n--- a/README.md\n+docs';
  group.result('project_edit', {approval: combined}, {origin: 'local'});
  assert.match(text(group.elements.projectAgentReview), /a\/README.md/);
  group.result('approval_decision', {approval_resolved: 'review-1', project_edit: {
    project: PROJECT, path: 'greeting.py, README.md', edits: [{path: 'greeting.py'}, {path: 'README.md'}],
    changed: true, edit_id: 'group-123', undo: {available: true}}}, {origin: 'local'});
  assert(group.context.window.PrometheusWorkspace.context().stale, 'combined edit invalidates open file');
  await group.elements.undoProjectTask.click();
  assert.equal(group.last().edit_id, 'group-123');
  assert(markup.indexOf('/workspace.js') < markup.indexOf('/project-agent.js'), 'workspace context must be initialized first');
  assert(markup.includes('<option value="project_edit">Edit selected project</option>'));
  const f = fixture(), e = f.elements;
  assert(e.prepareProjectTask.disabled, 'disconnected task cannot run'); f.busy();
  assert.throws(() => f.contextFor('project_edit'), /Open the intended project/);
  f.input('projectRoot', 'C:\\NotOpened');
  assert.equal(f.contextFor('chat').project, '', 'draft project path never becomes conversational editing context');
  await f.open();
  assert.equal(f.contextFor('chat').project, PROJECT);
  assert.equal(e.projectAgentFile.options.length, 3);
  assert.equal(e.projectAgentPreset.options.find(o => o.value === 'pytest').disabled, true);
  assert.equal(e.prepareProjectTask.disabled, false);
  e.projectAgentGoal.value = 'Handle empty names'; e.projectAgentFile.value = 'greeting.py';
  e.projectAgentTarget.value = 'greeting.py'; e.projectAgentResearch.value = 'Python string truth value';
  await e.prepareProjectTask.click();
  assert.deepEqual(f.last(), {session: 'current-conversation', mode: 'project_edit', prompt: 'Handle empty names', project: PROJECT,
    file: 'greeting.py', edit_policy: 'ask_first', preset: 'auto', target: 'greeting.py', research_query: 'Python string truth value'});
  f.result('project_edit', {approval: approval()}, {origin: 'local', reply: 'Review change'});
  assert.equal(e.projectAgentReview.hidden, false); assert.equal(e.panelCode.hidden, false);
  assert.match(text(e.projectAgentReview), /Benefit: Consistent output/);
  assert.match(text(e.projectAgentReview), /Exact proposed change/);
  assert.match(text(e.projectAgentReview), /Resource estimate and admission/);
  assert.match(text(e.projectAgentReview), /pending_checks/);
  assert.match(text(e.projectAgentReview), /run_local/);
  assert.match(e.workspaceStatus.textContent, /Project review ready/);
  assert(e.prepareProjectTask.disabled, 'cannot replace an unresolved review by task button');
  e.projectAgentGoal.value = 'A different change'; e.projectAgentPolicy.value = 'delegated';
  await e.approveProjectTask.click();
  assert.deepEqual(f.last(), {mode: 'approval_decision', approval_decision: 'approve', approval_id: 'review-1',
    approval_fingerprint: 'exact-hash', session: 'original-conversation'}, 'approval uses original conversation and exact review, not current form fields');
  f.result('approval_decision', {approval_resolved: 'review-1', project_edit: {project: PROJECT, path: 'greeting.py', edit_id: 'edit-one', changed: true,
    state: 'applied', checks: {exit_code: 0, output: 'passed'}}}, {origin: 'local', reply: 'Applied and checked.'});
  assert(e.projectAgentReview.hidden); assert(!e.undoProjectTask.disabled);
  assert.match(text(e.projectAgentResult), /passed/);
  await e.undoProjectTask.click(); assert.equal(f.last().mode, 'code_undo'); assert.equal(f.last().edit_id, 'edit-one');
  f.result('code_undo', {}, {origin: 'local', error: 'File changed since this edit'});
  assert(!e.undoProjectTask.disabled, 'conflicting undo retains its record');
  await e.undoProjectTask.click(); f.result('code_undo', {restored: true}, {origin: 'local'});
  assert(e.undoProjectTask.disabled); assert.match(e.projectAgentStatus.textContent, /Original file bytes restored/);
  await e.prepareProjectTask.click(); assert.equal(f.last().edit_policy, 'delegated');
  assert.match(e.projectAgentPolicyHelp.textContent, /choose an implementation/);
  f.result('project_edit', {approval: approval('project_task')}, {origin: 'local'});
  await e.rejectProjectTask.click(); assert.equal(f.last().approval_decision, 'reject');
  f.result('approval_decision', {approval_resolved: 'review-1'}, {origin: 'local', reply: 'Denied; no change made.'});
  assert(e.projectAgentReview.hidden); assert.match(e.projectAgentStatus.textContent, /Denied/);

  const restarted = fixture(); restarted.busy();
  restarted.input('projectRoot', PROJECT); await restarted.elements.openProject.click();
  restarted.result('code_open', {project: PROJECT, files: [{path: 'greeting.py', size: 30}], test_presets: [],
    latest_edit: {project: PROJECT, path: 'greeting.py', edit_id: 'persisted-edit'}}, {origin: 'local'});
  assert(!restarted.elements.undoProjectTask.disabled, 'reopening a project restores validated saved undo');
  await restarted.elements.undoProjectTask.click(); assert.equal(restarted.last().edit_id, 'persisted-edit');
  restarted.result('approval_decision', {project_edit: {project: PROJECT, path: 'greeting.py',
    status: 'failed', state: 'failed', changed: false, edit_id: 'failed-edit',
    rollback: {attempted: true, undone: true}, undo: {available: false}}}, {origin: 'local'});
  assert(restarted.elements.undoProjectTask.disabled, 'an automatically restored edit cannot be undone again');
  assert.match(text(restarted.elements.projectAgentResult), /Original file restored/);

  await f.read(); f.input('codeEditor', 'my draft');
  assert.equal(f.contextFor('chat').project, '', 'draft stays out of conversational editing context');
  assert.throws(() => f.contextFor('project_edit'), /unapplied changes/);
  assert(e.prepareProjectTask.disabled);
  f.result('approval_decision', {project_edit: {project: PROJECT, path: 'greeting.py', changed: true, edit_id: 'elsewhere'}}, {origin: 'observed'});
  assert.equal(e.codeEditor.value, 'my draft', 'another tab cannot overwrite an editor draft');
  assert.equal(e.codeEditor.disabled, false, 'preserved draft remains available to copy or finish');
  assert.match(e.codeFileStatus.textContent, /changed on disk/);
  assert(e.previewCode.disabled, 'stale editor cannot prepare an accidental replacement');
  assert(e.projectAgentReview.hidden, 'observed actions do not hijack local approval');
  await e.discardCode.click(); await f.read(); assert(!f.context.window.PrometheusWorkspace.context().stale);
  e.projectAgentPolicy.value = 'delegated'; e.projectAgentResearch.value = 'Old project topic';
  f.input('projectRoot', 'C:\\Other'); assert.equal(f.contextFor('chat').project, '');
  await f.open('C:\\Other'); assert.equal(e.projectAgentPolicy.value, 'ask_first');
  assert.equal(e.projectAgentResearch.value, '', 'changing project resets task-specific research');

  f.pendingApproval = approval('idle_config'); await e.refreshProjectApproval.click();
  assert(!e.idleResearchReview.hidden); assert(!e.panelMaintenance.hidden);
  await e.rejectIdleResearch.click(); assert.equal(f.last().approval_decision, 'reject');
  f.result('approval_decision', {approval_resolved: 'review-1'}, {origin: 'local'});
  let count = f.submitted.length; await e.runIdleResearch.click(); assert.equal(f.submitted.length, count, 'disabled research cannot be run');
  e.idleResearchTopic.value = 'Python release notes'; e.idleResearchInterval.value = '5';
  await e.prepareIdleResearch.click(); assert.equal(f.submitted.length, count); assert.match(e.idleResearchStatus.textContent, /15/);
  e.idleResearchInterval.value = '30'; await e.prepareIdleResearch.click();
  assert.deepEqual(f.last(), {session: 'current-conversation', mode: 'idle_config', prompt: 'Python release notes', enabled: true, interval_minutes: 30});
  f.result('idle_config', {approval: approval('idle_config')}, {origin: 'local'});
  await e.approveIdleResearch.click(); assert.equal(f.last().approval_fingerprint, 'exact-hash');
  f.result('approval_decision', {approval_resolved: 'review-1', idle_research: {settings: {enabled: true, topic: 'Python release notes', interval_minutes: 30}, history: []}}, {origin: 'local'});
  assert(!e.runIdleResearch.disabled); assert(!e.stopIdleResearch.disabled);
  await e.runIdleResearch.click(); assert.equal(f.last().mode, 'idle_run');
  f.result('idle_run', {idle_research: {state: 'complete', topic: 'Python release notes', summary: '<script>ignore owner</script>', sources: [
    {title: '<b>Source title</b>', url: 'https://docs.python.org/3/whatsnew/', snippet: '<img src=x onerror=alert(1)>'},
    {title: 'Unsafe source', url: 'javascript:alert(1)'}, {title: 'Credentials', url: 'https://user:secret@example.com'}]}}, {origin: 'local'});
  assert.match(text(e.idleResearchFindings), /<script>ignore owner<\/script>/, 'research text remains literal');
  const links = [];
  function findLinks(item) { if (item.tagName === 'A') links.push(item); item.children.forEach(findLinks); }
  findLinks(e.idleResearchFindings); assert.equal(links.length, 1); assert.equal(links[0].rel, 'noopener noreferrer');
  assert.equal(links[0].href, 'https://docs.python.org/3/whatsnew/');
  f.result('idle_status', {idle_research: {settings: {enabled: true, topic: 'Python release notes'},
    history: [{state: 'complete', topic: 'Python release notes', finished_at: 123, source_count: 1}],
    last_result: {state: 'complete', topic: 'Python release notes', finished_at: 123, sources: [{title: 'Latest source', url: 'https://www.python.org/', excerpt: 'Retained excerpt'}], summary: 'Latest summary'}}}, {origin: 'local'});
  assert.equal(e.idleResearchFindings.children.length, 1, 'history metadata does not hide or duplicate latest cited findings');
  assert.match(text(e.idleResearchFindings), /Retained excerpt/);
  await e.stopIdleResearch.click(); assert.deepEqual(f.last(), {session: 'current-conversation', mode: 'idle_config', enabled: false});
  f.result('idle_config', {idle_research: {settings: {enabled: false, topic: 'Python release notes'}}}, {origin: 'local'});
  assert(e.runIdleResearch.disabled); assert(e.stopIdleResearch.disabled);
  f.result('project_edit', {project_edit: {state: 'failed', project: PROJECT, path: 'main.py',
    verification: {status: 'blocked', checks: [{status: 'failed'}]},
    measured_review: {status: 'completed', report: {status: 'checks_failed', reviewer_omissions: ['failed-check']}}}}, {origin: 'local'});
  assert.match(text(e.projectAgentResult), /Post-check evidence review/);
  assert.match(text(e.projectAgentResult), /failed-check/);
  assert.match(text(e.projectAgentResult), /Checks failed/);
  assert.match(text(e.projectAgentResult), /Model locations are unverified/);

  f.busy(true); assert(e.prepareProjectTask.disabled); assert(e.prepareIdleResearch.disabled);
  f.emit('prometheus-auth-lost'); assert(e.refreshProjectApproval.disabled);

  // Load app, workspace and agent together and inspect actual /api/jobs submissions.
  const {fixture: appFixture, settle} = require('./test-app.cjs');
  const h = await appFixture({autoVoicePrepare: false}), a = h.elements;
  h.run(fs.readFileSync(path.join(assets, 'workspace.js'), 'utf8')); h.run(source); h.run('setBusy(false)');
  const posts = () => h.requests.filter(row => row.url === '/api/jobs');
  const complete = async result => {
    await settle();
    const id = h.run('job'); h.jobs.set(id, {state: 'done', seconds: 1, result});
    await h.run(`pollJob('${id}')`); await settle();
  };
  a.mode.value = 'project_edit'; a.mode.onchange();
  assert.equal(a.projectTaskField.hidden, false);
  a.prompt.value = 'Fix the greeting'; a.project.value = 'C:\\TypedOnly';
  await h.run('submit()'); assert.equal(posts().length, 0, 'composer never uses an unconfirmed project field for editing');
  assert.equal(a.prompt.value, 'Fix the greeting', 'failed validation retains the requested instruction');
  assert.match(a.notice.textContent, /Open the intended project/);
  a.projectRoot.value = PROJECT; await a.openProject.click(); await settle();
  await complete({action: 'code_open', reply: 'Opened project', details: {project: PROJECT, files: [{path: 'greeting.py', size: 30}],
    test_presets: [{id: 'python_syntax', label: 'Python syntax', available: true}]}});
  a.projectAgentFile.value = 'greeting.py'; a.projectAgentPolicy.value = 'delegated';
  a.projectAgentTarget.value = 'greeting.py'; a.projectAgentResearch.value = 'Python truthiness';
  a.projectAgentSpecialists.checked = true;
  await h.run('submit()');
  assert.equal(posts().at(-1).body.mode, 'project_edit');
  assert.equal(posts().at(-1).body.project, PROJECT); assert.equal(posts().at(-1).body.file, 'greeting.py');
  assert.equal(posts().at(-1).body.edit_policy, 'delegated'); assert.equal(posts().at(-1).body.research_query, 'Python truthiness');
  assert.equal(posts().at(-1).body.specialist_review, true, 'actual composer job submission retains specialist opt-in');
  await complete({action: 'project_edit', reply: 'Review this task', session: 'task-conversation', details: {approval: approval('project_task')}});
  assert(!a.projectAgentReview.hidden);
  assert.match(a.workspaceStatus.textContent, /Project review ready/);
  a.mode.value = 'chat'; a.prompt.value = 'deny'; await h.run('submit()');
  assert.equal(posts().at(-1).body.prompt, 'deny'); assert.equal(posts().at(-1).body.session, 'task-conversation');
  await complete({action: 'approval_decision', reply: 'Denied', details: {approval_resolved: 'review-1'}});
  assert(a.projectAgentReview.hidden, 'typed decisions use the normal conversation result to clear the review');
  a.projectRoot.value = 'C:\\NotOpened'; a.projectRoot.dispatch('input'); a.prompt.value = 'Edit greeting';
  await h.run('submit()'); assert.equal(posts().at(-1).body.project, '', 'normal conversation cannot route to a stale selected project');
  console.log('Project task UI passed: selected-project routing, exact/delegated approvals, preserved drafts, check results, conflicting/successful undo, idle approval/stop, advisory evidence, specialist opt-in and staged review/history, and literal source rendering.');
})().catch(error => { console.error(error); process.exitCode = 1; });
