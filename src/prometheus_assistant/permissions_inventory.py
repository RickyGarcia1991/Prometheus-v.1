"""Read-only visibility, not a new authorization or grant mechanism.

Core decisions come from the existing evaluator and registered ToolSpecs. Browser
handlers retain their separate checks. No executor, database, provider, model,
credential store or maintenance constructor is invoked by this module.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .guardrails import ToolRequest, evaluate_tool_request

SCHEMA_VERSION = 1
POLICY_SCOPE = 'Registered Core risk policy plus host deny restrictions; no plan approvals, scoped grants or custom guardrails supplied.'
GAPS = [
    'Shared application checks supplement existing Core/browser/CLI and provider boundaries; target checks remain separate.',
    'Signed host/bootstrap/controller/signing/recovery helpers retain independent OS/signature authority and cannot use application grants.',
    'Persistent grants are limited to exact research/public and project/tests requests, with a configured authenticated owner on the issuing host.',
    'Generic in-process Python extension executors remain trusted application code; this is not an OS sandbox for arbitrary Python callers.',
    'OS privileges, disk encryption, provider accounts, devices, installed models and physical portability are not probed.',
    'Update preferences, watch schedules, smart-home credentials and remote pending actions are not read by this view.',
]
SOURCE_FILES = (
    'application_policy.py', 'scoped_grants.py', 'host_authority.py',
    'guardrails.py', 'core.py', 'tool_registry.py', 'builtin_tools.py',
    'capabilities.py', 'ui_server.py', 'ui_workspace.py', 'owner_access.py',
    'coding_workspace.py', 'public_http.py', 'ollama.py', 'hosted_preview.py',
    'component_updates.py', 'remote_access.py', 'smart_home.py', 'voice.py',
    'owner_profile.py', 'worker_policy.py', 'personality.py', 'permissions_inventory.py',
)


def _row(identity, capability, category, outcome, reason, source, enforcement,
         *, scope, requirements, basis='source_review', grant='not_observed', adjustable=False):
    return dict(id=identity, capability=capability, category=category, outcome=outcome,
                reason=reason, scope=scope, policy_source=source, enforcement_point=enforcement,
                requirements=requirements, basis=basis, adjustable=adjustable,
                grant={'status': grant, 'expires_in_seconds': None})


def _registries():
    from .builtin_tools import build_builtin_registry
    from .capabilities import evidence_registry
    # These builders only register closures. None is intentionally unusable as
    # memory/app: discovery must never invoke one of the registered executors.
    return build_builtin_registry(None), evidence_registry(None)


def preview_core(body):
    """Validate registered arguments and evaluate policy, without authorizing work.

    Input risk flags, approval IDs, custom code and grant claims are rejected.
    Arguments and arbitrary exception text are never returned or persisted.
    """
    refusal = {'schema_version': SCHEMA_VERSION, 'outcome': 'deny',
               'authorizes_execution': False, 'scope': POLICY_SCOPE,
               'grant': {'status': 'none_supplied', 'expires_in_seconds': None}}
    if (not isinstance(body, dict) or set(body) != {'worker', 'tool', 'arguments'}
            or not isinstance(body['worker'], str) or not isinstance(body['tool'], str)
            or len(body['worker']) > 80 or len(body['tool']) > 80):
        return {**refusal, 'reason': 'Use exactly worker, tool and arguments for a registered Core tool.'}
    if not isinstance(body['arguments'], dict):
        return {**refusal, 'reason': 'Arguments must be an object matching the registered tool schema.'}
    registry = _registries()[0]
    try:
        spec = registry.get(body['worker'], body['tool'])
    except ValueError:
        return {**refusal, 'reason': 'Unknown Core capability.'}
    try:
        request = spec.request('Read-only policy preview', body['arguments'])
    except (ValueError, TypeError):
        return {**refusal, 'reason': 'Arguments do not match the registered tool schema.'}
    from .application_policy import registered_decision
    result = registered_decision(spec, request)
    return {**refusal, 'outcome': result.decision.value,
            'capability': spec.worker+'/'+spec.name,
            'reason': result.reason or 'Default Core policy allows this risk class.',
            'requirements': 'Execution still validates paths, targets and prerequisites. This is not an action approval.'}


def snapshot(*, owner=None):
    from .ui_server import MODES, BROWSER_PERMISSIONS_POLICY
    from . import ui_workspace
    rows = []
    core, evidence = _registries()
    core_catalog = core.structured_catalog()
    for entry in core_catalog:
        request = ToolRequest(entry['worker'], entry['name'], 'Inventory', **entry['risk'])
        result = evaluate_tool_request(request)
        from .application_policy import decision
        overlay = decision('core.'+entry['worker']+'.'+entry['name'])
        if overlay.decision.value == 'deny':
            result = overlay
        rows.append(_row('core.'+entry['worker']+'.'+entry['name'],
            entry['worker']+'/'+entry['name'], 'application', result.decision.value,
            result.reason or 'Default Core policy allows this risk class.',
            'guardrails.py:evaluate_tool_request; builtin_tools.py:build_builtin_registry',
            'core.py:authorize_plan / execute_authorized; tool-specific validators',
            scope=POLICY_SCOPE, requirements='Actual arguments, targets and resource availability are not checked by inventory.',
            basis='runtime_evaluator', grant='none_supplied'))
    for entry in evidence.structured_catalog():
        rows.append(_row('evidence.'+entry['name'], 'Evidence: '+entry['name'], 'application', 'conditional',
            'Registered read tool; browser invocation uses its own validation and owner gate.',
            'capabilities.py:evidence_registry', 'capabilities.py:retrieve; ui_server.py:Handler',
            scope='Selected evidence source', requirements='Selected project, imported documents or installed references; not probed.',
            basis='runtime_registry'))
    for mode in sorted(MODES):
        handler = 'ui_workspace.py:execute' if mode in ui_workspace.MODES else 'ui_server.py:Interface.execute'
        rows.append(_row('browser.'+mode, 'Browser: '+mode, 'application', 'conditional',
            'Registered browser mode. Explicit requests use the HTTP owner gate and mode-specific checks; Core approval is not inferred.',
            'ui_server.py:MODES; ui_workspace.py:MODES', handler,
            scope='Local browser job', requirements='Owner access when configured, valid request and action-specific prerequisites.',
            basis='runtime_registry'))
    if owner is None:
        outcome, reason = 'not_observed', 'Owner session is not observed by this standalone inventory.'
    elif not owner['required']:
        outcome, reason = 'not_configured', 'This instance has no configured HTTP owner gate; a passkey was not verified.'
    elif not owner['ready']:
        outcome, reason = 'unavailable', 'Owner verification dependencies or setup are unavailable; the HTTP gate stays locked.'
    elif owner['unlocked']:
        outcome, reason = 'allow', 'The current HTTP owner session is unlocked.'
    else:
        outcome, reason = 'deny', 'The current HTTP owner session is locked or expired.'
    owner_row = _row('identity.owner', 'HTTP owner access', 'authentication_integrity', outcome, reason,
        'owner_access.py:OwnerAccess', 'ui_server.py:Handler.owner_unlocked', scope='This browser session only',
        requirements='Normal owner passkey flow when configured. Does not authenticate CLI access or encrypt files.', basis='runtime_state')
    if owner and owner['required'] and owner['unlocked']:
        owner_row['grant'] = {'status': 'owner_session', 'expires_in_seconds': owner['expires_in_seconds']}
    rows.append(owner_row)
    rows.append(_row('identity.http', 'Local HTTP request boundary', 'authentication_integrity', 'enforced',
        'Host, origin, fetch-site and request-token checks remain active.',
        'ui_server.py:Handler.allowed', 'ui_server.py:Handler.do_GET / do_POST',
        scope='Local HTTP requests', requirements='Local interface address and token; POST also requires same origin.'))
    rows.append(_row('devices.browser_capture', 'Browser camera, microphone and location', 'application', 'deny',
        BROWSER_PERMISSIONS_POLICY, 'ui_server.py:BROWSER_PERMISSIONS_POLICY', 'ui_server.py:Handler._respond',
        scope='Browser capture APIs', requirements='Browser enforces the response header. Native dictation is a separate path.',
        basis='runtime_constant'))
    reviews = [
        ('files.project', 'Project files and tests', 'authentication_integrity', 'conditional',
         'Selected roots, private-file/link checks and stale-hash checks remain enforced. Tests execute with host user permissions.',
         'coding_workspace.py:CodingWorkspace; builtin_tools.py:build_builtin_registry',
         'CodingWorkspace._path / apply_edit / run_tests', 'Selected project', 'Trusted code and OS file access; process bounds are not an OS sandbox.'),
        ('network.public', 'Public publisher requests', 'application', 'conditional',
         'Registered HTTPS publisher, public DNS and transport checks remain enforced; browser research and Core network approval are distinct.',
         'public_http.py', 'validate_public_url / public_addresses', 'Registered publisher', 'Network/provider availability is not probed.'),
        ('models.local', 'Local model connection', 'application', 'conditional',
         'Only loopback model endpoints and local models are accepted; no automatic cloud fallback.',
         'ollama.py', 'OllamaClient / ensure_local_model', 'Local inference adapter', 'Installed qualified model, runtime and sufficient host resources; not probed.'),
        ('models.hosted', 'Hosted answer transport', 'external', 'unavailable',
         'The hosted preview helper has no network transport; a preview grants no permission to send data.',
         'hosted_preview.py', 'Offline preview only', 'Hosted preview helper', 'Separate implemented transport, approved scope and credentials would be required.'),
        ('updates.components', 'Component update policy', 'application', 'conditional',
         'Discovery and approved text imports retain their catalog checks. Executables, models and runtimes are not promoted here; current auto-check preference is not observed.',
         'component_updates.py', 'ComponentUpdates; ui_server.py:Interface.idle_maintenance', 'Catalog entries', 'Existing saved preferences, publisher availability and license rules.'),
        ('remote.actions', 'Remote computer actions', 'application', 'conditional',
         'Exact selected-device/action review, expiry and account/generation checks remain; uncertain effects are not automatically retried.',
         'remote_access.py', 'RemoteAccess.prepare / _execute', 'Selected paired device', 'Provider account, online device and current review; none are probed.'),
        ('devices.home', 'Smart home actions', 'application', 'conditional',
         'Configured device/action allowlist and control-key checks remain. A sent command does not confirm physical completion.',
         'smart_home.py', 'handle_command', 'Configured hub and named device', 'Existing hub, credentials and hardware; not read or probed.'),
        ('devices.native_voice', 'Native dictation', 'external', 'conditional',
         'Explicit native capture is separate from browser microphone denial.',
         'voice.py; ui_server.py:Interface.execute', 'capture_prompt', 'Host microphone', 'OS permission, hardware and installed voice runtime; not probed.'),
        ('advisory.memory', 'Personalization and worker instructions', 'advisory', 'advisory',
         'Profile context and tone/workflow declarations do not prove identity, grant permissions or provide OS isolation.',
         'owner_profile.py; worker_policy.py; personality.py', 'Prompt/context construction', 'Answer presentation', 'Actual model behavior still requires acceptance testing.'),
        ('external.host', 'Host and physical limits', 'external', 'not_observed',
         'OS access, disk encryption, provider licensing, real hardware and portable reinsertion have not been checked by this view.',
         'External host/provider controls', 'OS / provider / hardware', 'Current host and devices', 'Separate owner and physical acceptance.'),
    ]
    for identity, title, category, outcome, reason, source, enforcement, scope, requirements in reviews:
        rows.append(_row(identity, title, category, outcome, reason, source, enforcement,
                         scope=scope, requirements=requirements))
    fingerprints = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in SOURCE_FILES}
    revision = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    from .application_policy import inventory
    shared = inventory()
    for entry in shared['dispatch']:
        rows.append(_row('shared.'+entry['capability'], 'Application: '+entry['capability'],
                         'application', entry['outcome'], entry['reason'],
                         'application_policy.py:require_dispatch', entry['enforcement'],
                         scope='Shared application layer; explicit workflow authority and target checks remain',
                         requirements='No readiness, owner, provider or OS authority is conferred by this outcome.',
                         basis='runtime_evaluator', grant='unsupported'))
    return {'schema_version': SCHEMA_VERSION, 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'policy_revision': shared['revision'], 'source_sha256': fingerprints,
            'read_only': True, 'authorizes_execution': False, 'rows': rows,
            'preview_tools': core_catalog,
            'shared_application_policy': shared,
            'grant_management': {'supported_capabilities': ['research/public', 'project/tests'],
                                 'requires_enrolled_owner': True, 'max_seconds': 900, 'max_uses': 5,
                                 'storage': 'Host-local authority database with independent continuity witness; excluded from portable backups'},
            'coverage': {'complete': False, 'core_tools_evaluated': len(core_catalog),
                         'evidence_tools_listed': len(evidence.structured_catalog()),
                         'browser_modes_listed': len(MODES), 'boundary_rows': len(rows),
                         'unmapped_surfaces': GAPS},
            'notice': 'Point-in-time visibility only. Allow means the named policy layer allows it; it is not an action approval or a readiness check.'}
