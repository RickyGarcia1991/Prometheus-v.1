import threading
import pytest
from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.project_editor import ProjectEditor
from prometheus_assistant.project_change_set import apply_change_set
from prometheus_assistant.project_change_set import apply_group, undo_group, latest_group


@pytest.fixture
def task(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    for name in ('a.txt', 'b.txt'):
        (root / name).write_bytes(b'old\r\n')
    ws = CodingWorkspace(root, tmp_path / 'history', resources=tmp_path / 'resources')
    def generate(messages, schema):
        import json
        path = json.loads(messages[-1]['content'])['path']
        return {'path': path, 'reason': 'Requested update', 'benefit': 'Consistent files',
                'replacements': [{'old_text': 'old', 'new_text': 'new'}]}
    editor = ProjectEditor(ws, generate)
    return editor, [editor.propose('Update old to new', target=p) for p in ('a.txt', 'b.txt')]


def test_two_files_and_durable_individual_undo(task):
    editor, candidates = task
    result = apply_change_set(editor, candidates)
    assert result['status'] == 'applied_without_checks'
    for edit in result['edits']:
        assert editor.workspace.read_file(edit['path'])['content'].strip() == 'new'
        editor.workspace.undo_edit(edit['edit_id'])
        assert (editor.workspace.root / edit['path']).read_bytes() == b'old\r\n'


def test_stale_second_file_prevents_first_write(task):
    editor, candidates = task
    (editor.workspace.root / 'b.txt').write_text('newer')
    with pytest.raises(ValueError):
        apply_change_set(editor, candidates)
    assert (editor.workspace.root / 'a.txt').read_bytes() == b'old\r\n'
    assert not editor.workspace.history.exists()


@pytest.mark.parametrize('newer', [False, True])
def test_partial_failure_recovers_without_overwriting_newer_work(task, monkeypatch, newer):
    editor, candidates = task
    original = editor.workspace.apply_edit
    def apply(path, *args):
        if path == 'b.txt':
            if newer:
                (editor.workspace.root / 'a.txt').write_text('external edit')
            raise OSError('disk failure')
        return original(path, *args)
    monkeypatch.setattr(editor.workspace, 'apply_edit', apply)
    result = apply_change_set(editor, candidates)
    assert result['status'] == 'failed'
    assert result['recovery'][0]['undone'] is not newer
    assert (editor.workspace.root / 'a.txt').read_bytes() == (b'external edit' if newer else b'old\r\n')


def test_cancellation_after_first_write_recovers(task, monkeypatch):
    editor, candidates = task
    event = threading.Event()
    original = editor.workspace.apply_edit
    def apply(*args):
        result = original(*args)
        event.set()
        return result
    monkeypatch.setattr(editor.workspace, 'apply_edit', apply)
    result = apply_change_set(editor, candidates, cancel_event=event)
    assert result['status'] == 'cancelled'
    assert result['recovery'][0]['undone']


def test_duplicate_file_rejected_before_write(task):
    editor, candidates = task
    with pytest.raises(ValueError, match='same file'):
        apply_change_set(editor, [candidates[0], candidates[0]])
    assert not editor.workspace.history.exists()


def test_group_corrupt_backup_prevents_all_restores(task):
    editor, candidates = task
    result = apply_group(editor, {'candidates': candidates, 'path': 'a.txt, b.txt'})
    ws = editor.workspace
    assert latest_group(ws, ws.latest_edit())['edit_id'] == result['edit_id']
    (ws.history / (result['edits'][0]['edit_id'] + '.before')).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        undo_group(ws, result['edit_id'])
    for name in ('a.txt', 'b.txt'):
        assert ws.read_file(name)['content'].strip() == 'new'


@pytest.mark.parametrize('corrupt', ['null', '[]', '{"edits":[null]}'])
def test_malformed_group_does_not_break_project_reopening(task, corrupt):
    editor, candidates = task
    result = apply_group(editor, {'candidates': candidates, 'path': 'a.txt, b.txt'})
    ws = editor.workspace
    (ws.history / (result['edit_id'] + '.json')).write_text(corrupt)
    assert latest_group(ws, ws.latest_edit()) is None
    with pytest.raises(ValueError):
        undo_group(ws, result['edit_id'])
    assert ws.read_file('a.txt')['content'].strip() == 'new'


def test_group_receipt_write_failure_recovers(task, monkeypatch):
    from prometheus_assistant import project_change_set as module
    editor, candidates = task
    def fail(*args):
        raise OSError('receipt disk failure')
    monkeypatch.setattr(module, '_write_atomic', fail)
    result = apply_group(editor, {'candidates': candidates, 'path': 'a.txt, b.txt'})
    assert result['status'] == 'failed'
    assert result['rollback']['undone']
    assert not result['undo']['available']
    for name in ('a.txt', 'b.txt'):
        assert (editor.workspace.root / name).read_bytes() == b'old\r\n'


@pytest.mark.parametrize('expected', ['new', 'wrong'])
def test_real_checks_see_whole_change_set_and_failure_recovers(task, expected):
    editor, _ = task
    tests = editor.workspace.root / 'tests'
    tests.mkdir()
    (tests / 'test_pair.py').write_text(
        'import unittest\nfrom pathlib import Path\n'
        'class Pair(unittest.TestCase):\n'
        '    def test_both(self):\n'
        '        for name in ("a.txt", "b.txt"):\n'
        f'            self.assertEqual(Path(name).read_text().strip(), {expected!r})\n')
    candidates = [editor.propose('Update old to new', target=p,
        preset='python-unittest', test_target='tests') for p in ('a.txt', 'b.txt')]
    result = apply_change_set(editor, candidates)
    assert len(result['checks']) == 1
    assert result['status'] == ('passed' if expected == 'new' else 'failed')
    for name in ('a.txt', 'b.txt'):
        assert (editor.workspace.root / name).read_text().strip() == ('new' if expected == 'new' else 'old')
