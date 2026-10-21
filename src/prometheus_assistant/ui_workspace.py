"""Explicit interface actions for documents, project editing and maintenance."""
from pathlib import Path
import base64
import binascii
import os
from .hardware import detect_hardware, resource_root

MODES = {'documents', 'document_import', 'document_list', 'document_index', 'code_open', 'code_read',
         'code_preview', 'code_apply', 'code_undo', 'code_tests', 'maintenance', 'watch_source',
         'check_sources', 'backup', 'restore_check', 'model_benchmark'}
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
        elif len(value) > (12_000_000 if name == 'content' and body.get('mode') == 'document_import' else 256_000 if name == 'content' else 2000):
            raise ValueError(name+' exceeds its size limit.')


def library(app):
    from .document_library import DocumentLibrary, OllamaEmbeddingClient
    def allowed():
        free = detect_hardware().available_ram_gib
        return free is not None and free >= 2.75
    client = OllamaEmbeddingClient()
    return DocumentLibrary(app.data_root/'Library', embedding_client=client, embedding_allowed=allowed)


def schedule(app):
    from .maintenance import FreshnessSchedule
    return FreshnessSchedule(app.data_root/'Maintenance/watches.sqlite3', app.data_root/'PublicHistory')


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


def execute(app, request):
    mode = request['mode']
    result = None
    if mode == 'documents':
        result = library(app).search(request['prompt'], include_history=request.get('include_history', False))
        return {**app.save(request, result, 'Your documents · '+result['retrieval']), 'action': mode}
    if mode == 'document_import':
        try:
            data = base64.b64decode(request.get('content', ''), validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError('Invalid uploaded document encoding.') from error
        result = library(app).import_bytes(request.get('name', ''), data,
            source_url=request.get('source_url') or None, source_date=request.get('source_date') or None,
            document_id=request.get('document_id') or None)
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
            data_dirs={name: app.data_root/name for name in ('Library','Coding','Maintenance','PublicHistory','Routing')})
        app.last_backup = result
    elif mode == 'restore_check':
        from .maintenance import restore_backup
        result = restore_backup(request.get('backup_path', ''), request.get('destination', ''))
    from .ui_server import readable_result
    return {'reply': readable_result(result), 'details': result, 'label': mode.replace('_', ' ').capitalize(), 'action': mode}
