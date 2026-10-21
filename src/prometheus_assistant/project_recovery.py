"""Durable write-ahead snapshots for explicitly recovering interrupted edits."""
import base64
import json
import re
import uuid
from .coding_workspace import _no_links, _write_atomic, _sha, MAX_FILE_BYTES

MAX_JOURNAL_BYTES = 3_000_000


def _save(workspace, record):
    path = workspace.history / (record['edit_id'] + '.json')
    _no_links(path)
    workspace.history.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(record, ensure_ascii=True).encode()
    if len(raw) > MAX_JOURNAL_BYTES:
        raise ValueError('Recovery journal exceeds its bound.')
    _write_atomic(path, raw)


def begin(editor, candidates):
    if latest(editor.workspace):
        raise ValueError('Recover the interrupted project task before starting another edit.')
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
        raise ValueError('Invalid recovery change set.')
    rows, paths = [], set()
    for candidate in candidates:
        editor._validate_candidate(candidate)
        name = candidate['path']
        if name.casefold() in paths:
            raise ValueError('Duplicate recovery target.')
        paths.add(name.casefold())
        path, before, _ = editor.workspace._read(name)
        if _sha(before) != candidate['before_sha256']:
            raise ValueError('File changed before recovery snapshot.')
        rows.append({'path': name, 'before_sha256': _sha(before),
            'after_sha256': candidate['after_sha256'], 'before': base64.b64encode(before).decode(),
            'mode': path.stat().st_mode})
    record = {'version': 1, 'edit_id': 'recovery-' + uuid.uuid4().hex,
        'project': str(editor.workspace.root), 'state': 'running', 'files': rows}
    _save(editor.workspace, record)
    return record


def finish(workspace, record, result):
    rollback = result.get('rollback', {})
    conflict = (any(not row.get('undone') for row in result.get('recovery', []))
                or (rollback.get('attempted') and not rollback.get('undone')))
    record['state'] = 'needs_attention' if conflict else 'finished'
    _save(workspace, record)


def _load(workspace, edit_id):
    if not isinstance(edit_id, str) or not re.fullmatch(r'recovery-[a-f0-9]{32}', edit_id):
        raise ValueError('Invalid recovery identifier.')
    path = workspace.history / (edit_id + '.json')
    _no_links(path)
    if path.stat().st_size > MAX_JOURNAL_BYTES:
        raise ValueError('Recovery journal exceeds its bound.')
    record = json.loads(path.read_text())
    if (not isinstance(record, dict) or record.get('version') != 1 or record.get('edit_id') != edit_id
            or record.get('project') != str(workspace.root) or record.get('state') not in {'running', 'needs_attention'}):
        raise ValueError('No interrupted change is available in this journal.')
    rows = record.get('files')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise ValueError('Invalid recovery files.')
    paths = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str) or row['path'].casefold() in paths:
            raise ValueError('Invalid recovery target.')
        paths.add(row['path'].casefold())
        if not isinstance(row.get('before'), str) or type(row.get('mode')) is not int:
            raise ValueError('Invalid recovery snapshot.')
        before = base64.b64decode(row['before'], validate=True)
        if len(before) > MAX_FILE_BYTES or _sha(before) != row.get('before_sha256'):
            raise ValueError('Recovery snapshot checksum failed.')
        if not isinstance(row.get('after_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', row['after_sha256']):
            raise ValueError('Invalid recovery after hash.')
    return record


def latest(workspace):
    with workspace.lock:
        _no_links(workspace.history)
        for path in sorted(workspace.history.glob('recovery-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
            try:
                record = _load(workspace, path.stem)
                # Surface conflicts as pending recovery too; never hide uncertain writes.
                return {'project': str(workspace.root), 'edit_id': record['edit_id'],
                        'path': ', '.join(row['path'] for row in record['files']), 'interrupted': True}
            except (OSError, ValueError, TypeError, KeyError):
                continue
    return None


def restore(workspace, edit_id):
    from .application_policy import require_dispatch
    require_dispatch('workspace.undo')
    with workspace.lock:
        record = _load(workspace, edit_id)
        for row in record['files']:
            current = workspace.read_file(row['path'])['sha256']
            if current not in {row['before_sha256'], row['after_sha256']}:
                raise ValueError('Recovery conflict in ' + row['path'] + '; no files were restored.')
        restored = []
        for row in reversed(record['files']):
            path, current, _ = workspace._read(row['path'])
            if _sha(current) == row['before_sha256']:
                continue
            if _sha(current) != row['after_sha256']:
                raise ValueError('Recovery stopped after ' + str(len(restored)) + ' files; newer work was preserved.')
            before = base64.b64decode(row['before'], validate=True)
            _write_atomic(path, before, path.stat().st_mode)
            restored.append(row['path'])
        record['state'] = 'recovered'
        _save(workspace, record)
        return {'undone': True, 'recovered': True, 'files': restored,
                'path': ', '.join(row['path'] for row in record['files'])}
