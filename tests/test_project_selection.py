import json
import threading
import pytest
from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.project_editor import ProjectEditor
from prometheus_assistant.project_selection import select_files
from prometheus_assistant.ollama import GenerationCancelled


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    (root / 'greeting.py').write_text('def greet(): return "hello"\n')
    (root / 'test_greeting.py').write_text('def test_greet(): pass\n')
    return CodingWorkspace(root, tmp_path / 'history', resources=tmp_path / 'resources')


def test_select_related_files_from_bounded_real_catalog(project):
    def generate(messages, schema):
        catalog = json.loads(messages[-1]['content'])['catalog']
        assert {r['path'] for r in catalog} == {'greeting.py', 'test_greeting.py'}
        assert schema['properties']['files']['maxItems'] == 8
        return {'files': ['greeting.py', 'test_greeting.py']}
    editor = ProjectEditor(project, generate)
    assert select_files(editor, 'Fix greeting and its tests') == ['greeting.py', 'test_greeting.py']
    assert not project.history.exists()


@pytest.mark.parametrize('selection', [[], ['../outside.py'], ['greeting.py'] * 2,
    ['missing.py'], [None], 'greeting.py', ['greeting.py'] * 9])
def test_invalid_selection_never_writes(project, selection):
    editor = ProjectEditor(project, lambda *_: {'files': selection})
    with pytest.raises(ValueError): select_files(editor, 'Fix greeting')
    assert not project.history.exists()


def test_selection_cancellation(project):
    event = threading.Event()
    def generate(*_):
        event.set(); return {'files': ['greeting.py']}
    with pytest.raises(GenerationCancelled):
        select_files(ProjectEditor(project, generate), 'Fix greeting', cancel_event=event)
