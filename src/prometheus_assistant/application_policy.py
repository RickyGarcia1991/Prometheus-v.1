"""Shared application decision layer. Never replaces target or external authority.

The deny-only host override cannot authorize anything. Grant support is separate
and restricted to exact Core requests. OS callers remain responsible for their
existing local authority; an HTTP payload cannot select a principal or risk.
"""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from .guardrails import Decision, GuardrailResult, evaluate_tool_request

UI_MODES = frozenset('chat coding ask vision reach capabilities library calculate research project dictate tools models home documents document_import document_list document_index code_open code_read code_preview code_apply code_undo code_tests maintenance watch_source check_sources backup restore_check model_benchmark health_check component_updates check_updates configure_updates core'.split())
UI_MODES |= frozenset({'self_improve', 'approval_decision'})
CLI_COMMANDS = frozenset('permissions ui huggingface security-hardware research legal math library see voice-status transcribe coding-tools code media ask backup-memory restore-memory sources resources search sessions history doctor inspect-host chat voice-chat'.split())
ADAPTERS = frozenset('evidence.retrieve reach.read public.fetch ollama.request ollama.stream coding.launch media.launch home.request voice.record voice.transcribe voice.speak maintenance.sources maintenance.updates backup.create backup.restore remote.action remote.dispatch'.split())
ADAPTERS |= frozenset('workspace.apply workspace.undo workspace.tests documents.import documents.index documents.extract research.provider hf.metadata integrations.inspect archive.search remote.transport media.inspect ollama.start'.split())
SCOPED = frozenset({'maintenance.source.dispatch', 'maintenance.update.dispatch', 'remote.dispatch'})
CONTROL = frozenset('init doctor snapshot verify'.split())
CAPABILITIES = frozenset({'ui.'+x for x in UI_MODES} | {'cli.'+x for x in CLI_COMMANDS} | {'control.'+x for x in CONTROL} | set(ADAPTERS) | set(SCOPED))


def decision(capability):
    if capability not in CAPABILITIES and not capability.startswith('core.'):
        return GuardrailResult(Decision.DENY, 'Unmapped application capability.')
    denied = frozenset(x.strip() for x in os.environ.get('PROMETHEUS_DENY_CAPABILITIES', '').split(',') if x.strip())
    if capability in denied or '*' in denied:
        return GuardrailResult(Decision.DENY, 'This capability is denied by the host application policy.')
    return GuardrailResult(Decision.ALLOW, 'Application policy permits the existing workflow; its authority and target checks still apply.')


def scope_digest(scope):
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require_dispatch(capability, *, scope=None):
    # Core names must be resolved from a registry, never this string interface.
    if capability not in CAPABILITIES:
        raise ValueError('Unmapped application capability.')
    result = decision(capability)
    if result.decision is not Decision.ALLOW:
        raise ValueError(result.reason)
    if capability in SCOPED:
        if not isinstance(scope, dict) or not scope:
            raise ValueError('This dispatch requires its concrete selected target.')
        scoped_key = capability+':'+scope_digest(scope)
        denied = {value.strip() for value in os.environ.get('PROMETHEUS_DENY_SCOPES', '').split(',') if value.strip()}
        if scoped_key in denied:
            raise ValueError('Application policy denies this exact selected target.')
    return result


def registered_decision(spec, request, guardrails=()):
    canonical = spec.request(request.summary, dict(request.arguments or {}))
    if (dict(canonical.arguments) != dict(request.arguments or {}) or
            any(getattr(canonical, key) != getattr(request, key) for key in ('worker','tool','mutates_state','external_network','command_execution'))):
        raise ValueError('Request risk metadata or arguments differ from the registered capability.')
    result = decision('core.'+spec.worker+'.'+spec.name)
    if result.decision is Decision.DENY:
        return result
    return evaluate_tool_request(canonical, guardrails)


@lru_cache(maxsize=1)
def _source_revision():
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.glob('*.py')):
        digest.update(path.name.encode()+b'\0'+path.read_bytes())
    return digest.hexdigest()


def revision():
    # Cache immutable source, never effective host configuration. Order and
    # duplicate spelling do not change a deny set; a real policy change does.
    settings = {name: sorted({value.strip() for value in os.environ.get(name, '').split(',') if value.strip()})
                for name in ('PROMETHEUS_DENY_CAPABILITIES', 'PROMETHEUS_DENY_SCOPES')}
    return scope_digest({'source':_source_revision(), 'host_policy':settings})


def inventory():
    sites = {'evidence.retrieve':'capabilities.retrieve', 'public.fetch':'public_http.fetch_public_bytes',
             'workspace.apply':'CodingWorkspace.apply_edit','workspace.undo':'CodingWorkspace.undo_edit','workspace.tests':'CodingWorkspace.run_tests',
             'documents.import':'DocumentLibrary.import_bytes','documents.index':'DocumentLibrary.index_missing_embeddings','documents.extract':'document_library._extract',
             'research.provider':'HttpJsonResearchProvider.search','hf.metadata':'hf_resources.compare_remote','integrations.inspect':'integrations.launch_integrations',
             'archive.search':'archive_worker.search','remote.transport':'MCPBridge.__init__ before process creation',
             'ollama.request':'OllamaClient._request','ollama.stream':'OllamaClient.chat_stream',
             'coding.launch':'coding.launch_coding','media.launch':'media.launch_media','media.inspect':'media.nvidia_devices','ollama.start':'Interface.ensure_runtime before helper launch','home.request':'HomeClient.request',
             'voice.record':'voice.record_windows / continuous_capture.record_continuous','voice.transcribe':'voice.transcribe','voice.speak':'voice.speak',
             'maintenance.sources':'FreshnessSchedule.run_due_checks','maintenance.source.dispatch':'FreshnessSchedule.run_due_checks after lease/target selection',
             'maintenance.updates':'ComponentUpdates.run_due_checks','maintenance.update.dispatch':'ComponentUpdates.run_due_checks after lease/entry selection',
             'backup.create':'maintenance.create_backup','backup.restore':'maintenance.restore_backup',
             'remote.action':'RemoteAccess.action','remote.dispatch':'RemoteAccess._execute before selected provider dispatch'}
    return {'revision': revision(), 'dispatch': [
        {'capability': key, 'outcome': decision(key).decision.value,
         'reason': decision(key).reason, 'grant_supported': False,
         'enforcement': sites.get(key, 'Interface.submit / Interface.execute / workspace executor' if key.startswith('ui.') else 'cli.main' if key.startswith('cli.') else 'prometheus_control_center.cli.main')}
        for key in sorted(CAPABILITIES)],
        'external_authority': 'Signed host/bootstrap/controller/signing/recovery helpers and OS/provider authority remain independent.'}
