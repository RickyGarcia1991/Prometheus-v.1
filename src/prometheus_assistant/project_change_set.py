"""Execution of a bounded, caller-approved group of project candidates."""
from copy import deepcopy
import re
import json
import uuid

from .coding_workspace import _no_links, _write_atomic, _sha, MAX_FILE_BYTES

from .project_editor import _cancel
from .ollama import GenerationCancelled


def apply_change_set(editor, candidates, *, cancel_event=None, on_output=None, check_runner=None):
    """Preflight every edit, apply all, then check; preserve newer work on recovery.

    Approval belongs to the caller. Individual durable undo records are returned.
    This is recoverable execution, not filesystem-wide atomicity or crash resume.
    """
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
        raise ValueError('Review between one and eight file candidates.')
    candidates = deepcopy(candidates)
    workspace = editor.workspace
    result = {'status': 'passed', 'edits': [], 'checks': [], 'recovery': []}
    checks, paths = [], set()
    with workspace.lock:
        _cancel(cancel_event)
        for candidate in candidates:
            planned = editor._validate_candidate(candidate)
            path = candidate['path'].casefold()
            if path in paths:
                raise ValueError('A change set cannot edit the same file twice.')
            paths.add(path)
            for check in planned:
                if check not in checks:
                    checks.append(check)
        try:
            for candidate in candidates:
                _cancel(cancel_event)
                editor._stage('Applying reviewed change to ' + candidate['path'])
                result['edits'].append(workspace.apply_edit(candidate['path'],
                    candidate['content'], candidate['before_sha256']))
            if not any(row['changed'] for row in result['edits']):
                result['status'] = 'unchanged'
                return result
            def verify():
                for edit in result['edits']:
                    if workspace.read_file(edit['path'])['sha256'] != edit['sha256']:
                        raise ValueError('A change-set file has a newer edit; newer work will be preserved.')
            for check in checks:
                _cancel(cancel_event)
                verify()
                editor._stage('Running ' + check['preset'] + ' on ' + check['target'])
                evidence = (workspace.run_tests(check['preset'], check['target'],
                    cancel_event=cancel_event, on_output=on_output) if check_runner is None else
                    check_runner(workspace, check['preset'], check['target'],
                        cancel_event=cancel_event, on_output=on_output))
                count = re.search(r'Ran (\d+) tests?\b|(?:#|\u2139) tests (\d+)\b', evidence.get('output', ''))
                zero_tests = count and int(count.group(1) or count.group(2)) == 0
                passed = evidence.get('exit_code') == 0 and not (
                    evidence.get('cancelled') or evidence.get('timed_out') or zero_tests)
                result['checks'].append({**check, **evidence, 'passed': bool(passed)})
                if evidence.get('cancelled'):
                    raise GenerationCancelled('Change-set checks cancelled.')
                if not passed:
                    raise ValueError('A reviewed change-set check failed.')
            _cancel(cancel_event)
            verify()
            if not any(row['changed'] for row in result['edits']):
                result['status'] = 'unchanged'
            elif not checks:
                result['status'] = 'applied_without_checks'
        except Exception as exc:
            result.update(status='cancelled' if isinstance(exc, GenerationCancelled) else 'failed', error=str(exc))
            for edit in reversed(result['edits']):
                if not edit['changed']:
                    continue
                editor._stage('Recovering ' + edit['path'])
                try:
                    receipt = workspace.undo_edit(edit['edit_id'])
                    result['recovery'].append({'edit_id': edit['edit_id'], **receipt})
                except Exception as recovery_error:
                    result['recovery'].append({'edit_id': edit['edit_id'], 'path': edit['path'],
                        'undone': False, 'error': str(recovery_error)})
    return result


def propose_group(editor, goal, files, **options):
    if not isinstance(files, list) or not 2 <= len(files) <= 8 or any(not isinstance(p, str) for p in files):
        raise ValueError('Select two to eight existing files for a combined review.')
    if len({p.casefold() for p in files}) != len(files):
        raise ValueError('Select each file only once.')
    # Resolve every target before spending model time. Generation is read-only.
    for path in files:
        editor.workspace.read_file(path)
    candidates = [editor.propose(goal, target=path, **options) for path in files]
    checks = []
    for candidate in candidates:
        for check in candidate['checks']:
            if check not in checks:
                checks.append(check)
    return {'version': 2, 'candidates': candidates, 'path': ', '.join(c['path'] for c in candidates),
        'reason': '\n'.join(c['path'] + ': ' + c['reason'] for c in candidates),
        'benefit': '\n'.join(c['path'] + ': ' + c['benefit'] for c in candidates),
        'diff': '\n'.join(c['diff'] for c in candidates), 'checks': checks,
        'check_reason': 'All files are applied before these checks run; shared checks run once.'}


def apply_group(editor, candidate, **options):
    with editor.workspace.lock:
        from . import project_recovery
        journal = project_recovery.begin(editor, candidate['candidates'])
        result = _apply_group(editor, candidate, **options)
        project_recovery.finish(editor.workspace, journal, result)
        return result


def _apply_group(editor, candidate, **options):
    result = apply_change_set(editor, candidate['candidates'], **options)
    changed = [e for e in result['edits'] if e['changed']]
    result.update(changed=bool(changed), path=candidate['path'], undo={'available': False})
    if result['status'] in {'passed', 'applied_without_checks'} and changed:
        group_id = 'group-' + uuid.uuid4().hex
        record = {'project': str(editor.workspace.root), 'edit_id': group_id,
                  'path': candidate['path'], 'edits': changed}
        try:
            _no_links(editor.workspace.history)
            _write_atomic(editor.workspace.history / (group_id + '.json'), json.dumps(record).encode())
        except Exception as exc:
            result.update(status='failed', error='Group receipt could not be saved: ' + str(exc))
            for edit in reversed(changed):
                try:
                    result['recovery'].append(editor.workspace.undo_edit(edit['edit_id']))
                except Exception as error:
                    result['recovery'].append({'path': edit['path'], 'undone': False, 'error': str(error)})
        else:
            result.update(edit_id=group_id, undo={'available': True, 'edit_id': group_id})
    if result['recovery']:
        recovered = all(r.get('undone') for r in result['recovery'])
        result['rollback'] = {'undone': recovered, 'attempted': True}
        if recovered:
            result['changed'] = False
        else:
            result['rollback']['error'] = 'Some files have newer edits or unavailable backups; inspect recovery details.'
    return result


def _group(workspace, group_id):
    if not isinstance(group_id, str) or not re.fullmatch(r'group-[a-f0-9]{32}', group_id):
        raise ValueError('Invalid group undo identifier.')
    path = workspace.history / (group_id + '.json')
    _no_links(path)
    if path.stat().st_size > 32000:
        raise ValueError('Invalid group receipt size.')
    group = json.loads(path.read_text())
    if not isinstance(group, dict) or group.get('project') != str(workspace.root) or group.get('edit_id') != group_id:
        raise ValueError('Undo group belongs to another project.')
    edits = group.get('edits')
    if not isinstance(edits, list) or not 1 <= len(edits) <= 8:
        raise ValueError('Invalid undo group.')
    paths = set()
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get('path'), str):
            raise ValueError('Invalid group member.')
        edit_id = edit.get('edit_id', '')
        if not isinstance(edit_id, str) or not re.fullmatch(r'[a-f0-9]{32}', edit_id):
            raise ValueError('Invalid group member.')
        record_path = workspace.history / (edit_id + '.json')
        backup = workspace.history / (edit_id + '.before')
        _no_links(record_path); _no_links(backup)
        if record_path.stat().st_size > 16000 or backup.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Invalid undo backup size.')
        record = json.loads(record_path.read_text())
        if (not isinstance(record, dict) or record.get('undone') or record.get('project') != str(workspace.root)
                or record.get('edit_id') != edit_id or record.get('path') != edit.get('path')
                or record.get('after_sha256') != edit.get('sha256')):
            raise ValueError('A group member cannot be undone.')
        if edit['path'].casefold() in paths:
            raise ValueError('Duplicate undo target.')
        paths.add(edit['path'].casefold())
        if workspace.read_file(edit['path'])['sha256'] != record['after_sha256']:
            raise ValueError('A group file changed; no grouped undo was started.')
        if _sha(backup.read_bytes()) != record['before_sha256']:
            raise ValueError('A group backup failed its checksum.')
    return group


def undo_group(workspace, group_id):
    with workspace.lock:
        group = _group(workspace, group_id)
        receipts = []
        for edit in reversed(group['edits']):
            try:
                receipts.append(workspace.undo_edit(edit['edit_id']))
            except Exception as exc:
                raise ValueError('Grouped undo stopped after ' + str(len(receipts)) +
                    ' restored files. Remaining files were preserved: ' + str(exc)) from exc
        return {'undone': True, 'path': group['path'], 'files': receipts}


def latest_group(workspace, latest):
    if not latest:
        return None
    with workspace.lock:
        _no_links(workspace.history)
        for path in sorted(workspace.history.glob('group-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
            try:
                group = _group(workspace, path.stem)
                if any(e['edit_id'] == latest['edit_id'] for e in group['edits']):
                    return {k: group[k] for k in ('project', 'path', 'edit_id')}
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return None
