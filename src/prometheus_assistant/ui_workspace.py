"""Explicit interface actions for documents, project editing and maintenance."""
from pathlib import Path
import base64
import binascii
import os
from .hardware import detect_hardware, resource_root

MODES = {'documents', 'document_import', 'document_list', 'document_index', 'code_open', 'code_read',
         'code_preview', 'code_apply', 'code_undo', 'code_tests', 'maintenance', 'watch_source',
         'check_sources', 'backup', 'restore_check', 'model_benchmark', 'health_check',
         'component_updates', 'check_updates', 'configure_updates'}
FIELDS = {'content', 'name', 'source_url', 'source_date', 'document_id', 'include_history', 'file',
          'expected_sha256', 'edit_id', 'preset', 'target', 'destination', 'backup_path', 'enabled'}
NO_PROMPT = MODES - {'documents', 'watch_source'}


def validate(body):
    for name in FIELDS:
        if name not in body:
            continue
        value = body[name]
        if name in {'include_history', 'enabled'}:
            if type(value) is not bool:
                raise ValueError(name+' must be true or false.')
        elif not isinstance(value, str) or '\x00' in value:
            raise ValueError('Invalid '+name+'.')
        elif len(value) > (12_000_000 if name == 'content' and body.get('mode') in {'document_import', 'vision'} else 256_000 if name == 'content' else 2000):
            raise ValueError(name+' exceeds its size limit.')


def library(app, *, semantic=True):
    from .document_library import DocumentLibrary, OllamaEmbeddingClient
    def allowed():
        free = detect_hardware().available_ram_gib
        return free is not None and free >= 2.75
    client = OllamaEmbeddingClient() if semantic else None
    return DocumentLibrary(app.data_root/'Library', embedding_client=client, embedding_allowed=allowed)


def schedule(app):
    from .maintenance import FreshnessSchedule
    return FreshnessSchedule(app.data_root/'Maintenance/watches.sqlite3', app.data_root/'PublicHistory')


def component_updates(app):
    import json
    from .component_updates import ComponentUpdates
    catalog = json.loads(Path(__file__).with_name('component_updates.json').read_text(encoding='utf-8'))
    return ComponentUpdates(app.data_root/'Maintenance/Updates', catalog)


def workspace(app, request):
    from .coding_workspace import CodingWorkspace
    root = request.get('project')
    if not root:
        raise ValueError('Select a project folder first.')
    drive = Path(resource_root()).parent if resource_root() else None
    protected = [app.memory_path.parent]
    if drive:
        protected += [drive/'Prometheus-Recovery', *drive.glob('Prometheus-v*')]
    return CodingWorkspace(root, app.data_root/'Coding', protected_roots=protected)


def result_summary(mode, result):
    """Keep routine workbench activity readable; full evidence stays in details."""
    if mode == 'health_check':
        return result['summary']
    if mode == 'document_list':
        return f"Document library refreshed: {len(result.get('documents', []))} saved versions. Open Documents to browse them."
    if mode == 'document_import':
        action = 'Existing version retained' if result.get('duplicate') else 'Document imported'
        return f"{action}: {result.get('title', 'document')}. {result.get('passage_count', 0)} searchable passages. Meaning index: {result.get('embedding_status', 'pending').replace('_', ' ')}."
    if mode == 'document_index':
        return f"Meaning index: {result.get('status', 'unknown').replace('_', ' ')}. {result.get('indexed', 0)} passages indexed; {result.get('pending', 0)} pending. Keyword search remains available."
    if mode == 'code_open':
        return f"Project opened: {len(result.get('files', []))} supported files. Choose a file in the Code workbench."
    if mode == 'code_read':
        return f"Opened {result['path']} in the editor. Changes are held for review before applying."
    if mode == 'code_preview':
        return f"Changes to {result['path']} are ready to review in Code. Nothing has been applied." if result['changed'] else 'The editor matches the file; no changes to apply.'
    if mode == 'code_apply':
        return f"Reviewed changes applied to {result['path']}. Undo is available in Code." if result['changed'] else 'No file changes were needed.'
    if mode == 'code_undo':
        return f"Restored the saved version of {result['path']}. Open the file again to review it."
    if mode == 'code_tests':
        status = 'cancelled' if result.get('cancelled') else 'timed out' if result.get('timed_out') else 'passed' if result.get('exit_code') == 0 else 'failed'
        return f"Project check {status}. Review its output in Code."
    if mode == 'maintenance':
        paused = len(result.get('model_evidence', {}).get('paused_roles', []))
        return f"Readiness refreshed. {paused} helper roles remain paused by their recorded task checks. Review hardware and backup details in Maintenance."
    if mode in {'component_updates', 'configure_updates', 'check_updates'}:
        rows = result.get('entries', [])
        def needs_attention(row):
            return bool(row.get('error') or row.get('pending_recovery') or row.get('status') in {'check_failed', 'rolled_back', 'cancelled'})
        def manually_updated(row):
            return row.get('provider') == 'pinned' or row.get('status') == 'check_unavailable'
        failures = sum(needs_attention(row) for row in rows)
        candidates = sum(not needs_attention(row) and not manually_updated(row) and row.get('status') in {'update_available', 'upstream_changed', 'staged_verified'} for row in rows)
        manual = sum(manually_updated(row) and not needs_attention(row) for row in rows)
        state = 'enabled' if result.get('enabled') else 'paused'
        return f"Automatic update checks {state}. {failures} checks need attention; {candidates} updates need review; {manual} entries use manual updates. See Maintenance for versions and next steps."
    if mode == 'model_benchmark':
        checked, passed = result.get('checked', 0), result.get('passed', 0)
        state = result.get('state', 'unknown').replace('_', ' ')
        return f"Helper checks: {passed} of {checked} scored cases passed; {checked - passed} failed. Run status: {state}. Review skipped cases and individual results in Maintenance. These checks do not establish overall accuracy."
    if mode == 'watch_source':
        return f"Source watch {'enabled' if result['enabled'] else 'disabled'} for {result['provider']}: {result['query']}."
    if mode == 'check_sources':
        checks = result.get('checks', [])
        failures = sum(bool(row.get('error')) for row in checks)
        return f"Source checks: {len(checks)} checked; {failures} failed. Earlier saved versions are retained." + (f" {result['reason']}" if result.get('reason') else '')
    if mode in {'backup', 'restore_check'}:
        label = 'Backup' if mode == 'backup' else 'Restore check'
        return f"{label} {'verified' if result.get('verified') else 'needs review'}. Review the destination and full result in Maintenance."
    raise ValueError('No workbench summary for this action.')


def execute(app, request):
    mode = request['mode']
    from .application_policy import require_dispatch
    if mode not in MODES:
        raise ValueError('Unmapped workspace capability.')
    require_dispatch('ui.'+mode)
    result = None
    if mode == 'documents':
        result = library(app).search(request['prompt'], include_history=request.get('include_history', False))
        return {**app.save(request, result, 'Your documents · '+result['retrieval']), 'action': mode}
    if mode == 'document_import':
        try:
            data = base64.b64decode(request.get('content', ''), validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError('Invalid uploaded document encoding.') from error
        result = library(app, semantic=False).import_bytes(request.get('name', ''), data,
            source_url=request.get('source_url') or None, source_date=request.get('source_date') or None,
            document_id=request.get('document_id') or None, cancel_event=app.cancel_event)
    elif mode == 'document_list':
        result = {'documents': library(app).list_documents(include_history=True)}
    elif mode == 'document_index':
        if (detect_hardware().available_ram_gib or 0) >= 2.75:
            app.ensure_runtime()
        result = library(app).index_missing_embeddings(limit=32)
    elif mode == 'model_benchmark':
        from .model_benchmarks import run_benchmarks
        from .ui_server import installed_models
        from .hardware import select_model
        names = installed_models()
        if select_model(names, detect_hardware()) is not None:
            app.ensure_runtime()
        result = run_benchmarks(app.model_evidence, names, cancel_event=app.cancel_event, on_stage=app.progress)
    elif mode.startswith('code_'):
        project = workspace(app, request)
        if mode == 'code_open':
            result = project.describe()
        elif mode == 'code_read':
            result = project.read_file(request.get('file', ''))
        elif mode in {'code_preview', 'code_apply'}:
            action = project.preview_edit if mode == 'code_preview' else project.apply_edit
            result = action(request.get('file', ''), request.get('content', ''), request.get('expected_sha256', ''))
        elif mode == 'code_undo':
            result = project.undo_edit(request.get('edit_id', ''))
        elif mode == 'code_tests':
            result = project.run_tests(request.get('preset', ''), request.get('target', ''),
                                       cancel_event=app.cancel_event, on_output=app.append_partial)
    elif mode == 'health_check':
        from .health_check import run_health_check
        result = run_health_check(app.memory_path, resource_root())
    elif mode == 'component_updates':
        result = component_updates(app).status()
    elif mode == 'configure_updates':
        if 'enabled' not in request:
            raise ValueError('Choose whether automatic update checks are enabled.')
        result = component_updates(app).configure(enabled=request['enabled'])
    elif mode == 'check_updates':
        app.progress('Checking one component for updates')
        result = component_updates(app).run_due_checks(max_checks=1, force=request.get('enabled', False),
            cancel_event=app.cancel_event, library=library(app, semantic=False))
    elif mode == 'maintenance':
        from .maintenance import readiness_report
        result = {'readiness': readiness_report(app.memory_path.parent, resource_root()), 'watches': schedule(app).status(),
                  'model_evidence': app.model_evidence.summary(), 'backup': app.last_backup}
    elif mode == 'watch_source':
        result = schedule(app).watch(request.get('provider', 'crossref'), request['prompt'], enabled=request.get('enabled', True))
    elif mode == 'check_sources':
        result = schedule(app).run_due_checks(online=True, max_checks=1)
    elif mode == 'backup':
        from .maintenance import create_backup
        destination = request.get('destination') or os.environ.get('PROMETHEUS_BACKUP_ROOT')
        if not destination:
            raise ValueError('Choose a backup folder on a different drive.')
        result = create_backup(app.memory_path, destination,
            data_dirs={**{name: app.data_root/name for name in ('Library','Coding','Maintenance','PublicHistory','Routing')},
                       'AgentMemory': app.data_root/'Integrations/AgentMemory'})
        app.last_backup = result
    elif mode == 'restore_check':
        from .maintenance import restore_backup
        result = restore_backup(request.get('backup_path', ''), request.get('destination', ''))
    return {'reply': result_summary(mode, result),
            'details': result, 'label': mode.replace('_', ' ').capitalize(), 'action': mode}
