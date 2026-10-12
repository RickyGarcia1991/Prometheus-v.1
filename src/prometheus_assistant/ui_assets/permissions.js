(function () {
'use strict';
const element = id => document.getElementById(id);
let local = {online: false, closed: false, authLost: false}, data = null, requesting = false, epoch = 0;
function node(tag, text, className) {
  const result = document.createElement(tag); result.textContent = String(text ?? '');
  if (className) result.className = className;
  return result;
}
function controls() {
  const disabled = !local.online || local.closed || local.authLost || requesting;
  element('refreshPermissions').disabled = disabled;
  element('previewPermission').disabled = disabled || !data;
  element('permissionTool').disabled = disabled || !data;
  element('permissionArguments').disabled = disabled || !data;
}
function clear(message) {
  epoch++; requesting = false; data = null;
  element('permissionRows').replaceChildren(); element('permissionCoverage').textContent = '';
  element('permissionTool').replaceChildren(); element('permissionArguments').value = '{}';
  element('permissionSchema').textContent = ''; element('permissionCount').textContent = '';
  element('permissionPreview').textContent = ''; element('permissionStatus').textContent = message;
  controls();
}
function render() {
  if (!data) return;
  const query = element('permissionSearch').value.trim().toLowerCase();
  const category = element('permissionCategory').value;
  const rows = data.rows.filter(row => (!category || row.category === category) &&
    (!query || [row.capability, row.reason, row.outcome, row.scope, row.policy_source].join(' ').toLowerCase().includes(query)));
  const cards = rows.map(row => {
    const card = node('details', '', 'workspace-card permission-row');
    card.append(node('summary', row.capability + ' · ' + row.outcome.replaceAll('_', ' ')));
    card.append(node('p', row.reason));
    const fields = [
      ['Scope', row.scope], ['Basis', row.basis.replaceAll('_', ' ')],
      ['Policy source', row.policy_source], ['Enforcement point', row.enforcement_point],
      ['Current grant', row.grant.status.replaceAll('_', ' ')],
      ['Expiry', row.grant.expires_in_seconds === null ? 'No grant expiry observed' : Math.ceil(row.grant.expires_in_seconds) + ' seconds remaining at snapshot'],
      ['Adjustable here', row.adjustable ? 'Yes' : 'No'], ['Requirements', row.requirements]
    ];
    for (const [label, value] of fields) card.append(node('p', label + ': ' + value, 'fine'));
    return card;
  });
  element('permissionRows').replaceChildren(...cards);
  element('permissionCount').textContent = rows.length + ' of ' + data.rows.length + ' rows shown';
}
async function load() {
  if (!local.online || local.closed || local.authLost || requesting) return;
  const current = ++epoch; requesting = true; controls();
  element('permissionStatus').textContent = 'Reading boundary inventory…';
  element('permissionPreview').textContent = '';
  try {
    const result = await api('/api/permissions');
    if (current !== epoch) return;
    data = result;
    element('permissionStatus').textContent = result.notice + ' Observed ' + result.generated_at_utc;
    const coverage = result.coverage;
    element('permissionCoverage').textContent = 'Coverage is partial: ' + coverage.core_tools_evaluated +
      ' Core tools evaluated, ' + coverage.evidence_tools_listed + ' evidence tools and ' + coverage.browser_modes_listed +
      ' browser modes listed. Policy revision: ' + result.policy_revision + '\n' + coverage.unmapped_surfaces.join('\n');
    if (result.shared_application_policy) {
      element('permissionCoverage').textContent += '\n' + result.shared_application_policy.dispatch.length +
        ' shared application dispatch checks are included in the table. Host denials are reflected; target-specific, owner, provider and OS checks still apply.';
    }
    element('permissionTool').replaceChildren(...result.preview_tools.map(tool => {
      const option = node('option', tool.worker + '/' + tool.name); option.value = tool.worker + '/' + tool.name; return option;
    }));
    schema(); render();
  } catch (error) {
    if (current === epoch) clear(error.message || 'Boundary inventory unavailable.');
  } finally { if (current === epoch) { requesting = false; controls(); } }
}
function schema() {
  element('permissionPreview').textContent = '';
  const tool = data?.preview_tools.find(item => item.worker + '/' + item.name === element('permissionTool').value);
  element('permissionSchema').textContent = tool ? JSON.stringify(tool.inputSchema, null, 2) : '';
}
async function preview() {
  if (!data || requesting || !local.online || local.closed || local.authLost) return;
  const [worker, tool] = element('permissionTool').value.split('/');
  let args;
  try { args = JSON.parse(element('permissionArguments').value); }
  catch { element('permissionPreview').textContent = 'Enter valid JSON arguments.'; return; }
  const current = ++epoch; requesting = true; controls();
  element('permissionPreview').textContent = 'Evaluating default Core policy…';
  try {
    const result = await api('/api/permissions/preview', {worker, tool, arguments: args});
    if (current !== epoch) return;
    element('permissionPreview').textContent = result.outcome + ': ' + result.reason + '\n' + result.scope +
      '\nThis preview authorizes no execution. Target and resource checks still apply.';
  } catch (error) {
    if (current === epoch) element('permissionPreview').textContent = error.message || 'Preview unavailable.';
  } finally { if (current === epoch) { requesting = false; controls(); } }
}
element('refreshPermissions').onclick = load;
element('permissionSearch').oninput = render; element('permissionCategory').onchange = render;
element('permissionTool').onchange = schema;
element('permissionArguments').oninput = () => { element('permissionPreview').textContent = ''; };
element('previewPermission').onclick = preview;
element('tabPermissions').addEventListener('click', load);
element('tabPermissions').addEventListener('focus', () => { if (!data) load(); });
element('ownerLock').addEventListener('click', () => clear('Owner lock requested; refresh after unlocking.'));
window.addEventListener('prometheus-auth-lost', () => {
  local.authLost = true;
  clear('Owner session ended; unlock again to inspect boundaries.');
});
window.addEventListener('prometheus-busy', event => {
  const wasOnline = local.online; local = {...local, ...event.detail};
  if (!local.online || local.closed || local.authLost) clear(local.closed ? 'Prometheus is closed.' : 'Local connection unavailable.');
  else if (!wasOnline && !element('panelPermissions').hidden) load();
  controls();
});
controls();
})();
