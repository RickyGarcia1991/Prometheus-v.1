"""Independent metadata acceptance edges; isolated databases and no model/network."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import capabilities, compact_chat, ui_server, ui_workspace
from prometheus_assistant.document_library import DocumentLibrary
from prometheus_assistant.hardware import ModelProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.ollama import LocalModelError


def test_documents_mode_keeps_inspectable_version_and_hash_after_reopen(tmp_path, monkeypatch):
    app = ui_server.Interface(tmp_path/'memory.sqlite3')
    library = DocumentLibrary(app.data_root/'Library')
    monkeypatch.setattr(ui_workspace, 'library', lambda *args, **kwargs: library)
    library.import_bytes('board-guide.md', b'The regulator input range is 8 to 20 volts.',
                         source_url='https://example.org/board/revision-7')
    try:
        result = app.execute(ui_server.validate_job({'mode': 'documents', 'prompt': 'regulator input range'}))
        original = result['details']['results'][0]
        with MemoryStore(app.memory_path) as memory:
            restored = memory.history(result['session'], include_metadata=True)[-1]
        saved_sources = restored['details'].get('results', restored['details'].get('sources', []))
        assert saved_sources, 'Documents-mode source records disappear from saved details'
        assert saved_sources[0]['sha256'] == original['sha256']
        assert saved_sources[0]['version_id'] == original['version_id']
        assert saved_sources[0]['excerpt'] == original['excerpt']
    finally:
        app.close()


@pytest.mark.parametrize('url_length', [696, 2000])
def test_valid_long_source_url_is_not_changed_by_retrieval_or_persistence(tmp_path, url_length):
    library = DocumentLibrary(tmp_path/'Library')
    prefix, suffix = 'https://example.org/manual/', '?revision=7#regulator'
    source_url = prefix + 'p' * (url_length-len(prefix)-len(suffix)) + suffix
    library.import_bytes('long-reference.md', b'The regulator input range is 8 to 20 volts.', source_url=source_url)
    result = capabilities.retrieve(SimpleNamespace(data_root=tmp_path),
                                   {'source': 'documents', 'prompt': 'regulator input range'})
    selected = result['sources'][0]
    assert selected['location'].split(' | ', 1)[0] == source_url, 'Saved citation must retain the actual imported URL'
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        sid = memory.create_session('references')
        memory.save_exchange(sid, 'Input range?', '8 to 20 volts.', details=result)
        assert memory.history(sid, include_metadata=True)[-1]['details']['sources'][0]['location'] == selected['location']


def test_search_result_metadata_does_not_persist_internal_paths_or_request_fields(tmp_path):
    source_url = 'https://example.org/manual/revision-7#power'
    private = 'fixture-private-image-or-auth-value'
    result = {'title': 'Guide', 'source_url': source_url, 'location': 'page 3',
              'excerpt': 'Only the documented range.', 'sha256': 'a'*64, 'version_id': 7,
              'original_path': private, 'content': private, 'images': [private],
              'token': private, 'request': {'authorization': private}}
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        sid = memory.create_session('references')
        memory.save_exchange(sid, 'Question', 'Answer', details={'results': [result]})
        saved = memory.history(sid, include_metadata=True)[-1]['details']['results'][0]
        assert saved == {k: result[k] for k in ('title', 'source_url', 'location', 'excerpt', 'sha256', 'version_id')}
        assert private not in memory.db.execute('SELECT details_json FROM turn_metadata').fetchone()[0]


def test_two_writers_cannot_attach_metadata_to_another_exchange(tmp_path):
    path = tmp_path/'memory.sqlite3'
    with MemoryStore(path) as memory:
        sid = memory.create_session('test')
    barrier = threading.Barrier(2)

    def writer(identity):
        with MemoryStore(path) as memory:
            barrier.wait(timeout=3)
            for index in range(4):
                name = f'{identity}-{index}'
                memory.save_exchange(sid, 'Question '+name, 'Answer '+name,
                                     label=name, details={'model': name})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer, name) for name in ('one', 'two')]
        for future in futures:
            future.result(timeout=8)
    with MemoryStore(path) as memory:
        rows = memory.history(sid, include_metadata=True)
        assert len(rows) == 16
        for user, assistant in zip(rows[::2], rows[1::2]):
            assert user['role'] == 'user' and assistant['role'] == 'assistant'
            assert user['content'][9:] == assistant['content'][7:] == assistant['label']
            assert assistant['details']['model'] == assistant['label']
        assert memory.db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_previous_v2_writer_can_append_without_losing_new_metadata(tmp_path):
    path = tmp_path/'memory.sqlite3'
    with MemoryStore(path) as memory:
        sid = memory.create_session('test')
        memory.save_exchange(sid, 'New question', 'New answer', label='New helper', details={'model': 'new'})
        first = memory.history(sid, include_metadata=True)
    # SQL used by the retained version-2 save_exchange, including foreign-key enforcement.
    with sqlite3.connect(path) as older:
        older.execute('PRAGMA foreign_keys=ON')
        assert older.execute('PRAGMA user_version').fetchone()[0] == 2
        older.executemany('INSERT INTO turns(session_id, role, content, created_at) VALUES (?, ?, ?, ?)',
                          [(sid, 'user', 'Rollback question', '2026-01-01'),
                           (sid, 'assistant', 'Rollback answer', '2026-01-01')])
    with MemoryStore(path) as memory:
        rows = memory.history(sid, include_metadata=True)
        assert rows[:2] == first
        assert len(rows) == 4 and 'details' not in rows[-1]


def test_history_handler_keeps_latest_40_with_metadata_and_rejects_unknown_ids(tmp_path):
    path = tmp_path/'memory.sqlite3'
    with MemoryStore(path) as memory:
        sid = memory.create_session('test')
        for index in range(23):
            memory.save_exchange(sid, str(index), 'Answer '+str(index), label='Helper '+str(index),
                                 details={'model': 'model-'+str(index)})
    responses = []
    handler = object.__new__(ui_server.Handler)
    handler.server = SimpleNamespace(app=SimpleNamespace(memory_path=path), owner_access=SimpleNamespace(required=False))
    handler.allowed = lambda **kwargs: True
    handler.owner_unlocked = lambda: True
    handler.respond = lambda code, body, *args: responses.append((code, body))
    handler.path = '/api/history/'+sid
    handler.do_GET()
    code, rows = responses[-1]
    assert code == 200 and len(rows) == 40
    assert rows[0]['content'] == '3' and rows[-1]['details']['model'] == 'model-22'
    assert rows[-1]['label'] == 'Helper 22'
    for path in ('/api/history/not-an-id', '/api/history/'+'0'*32):
        handler.path = path
        handler.do_GET()
        assert responses[-1][0] == 400
        assert 'Answer' not in json.dumps(responses[-1][1])


def test_failed_attempt_and_success_metrics_survive_without_losing_provenance(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compact_chat.OllamaClient, 'ensure_local_model', lambda self: None)

    def reply(client, messages, **kwargs):
        calls.append(client.model)
        if len(calls) == 1:
            raise LocalModelError('Synthetic model failure')
        client.last_metrics = {'model': client.model, 'seconds': 2.5, 'eval_count': 14,
                               'prompt_eval_count': 40, 'prompt_eval_seconds': .2, 'eval_seconds': 2.3}
        return 'The permitted input range is 8 to 20 volts [S1].', 14

    monkeypatch.setattr(compact_chat.OllamaClient, 'chat_stream', reply)
    source = {'id': 'S1', 'title': 'Revision 7', 'location': 'https://example.org/pinned/manual#power',
              'excerpt': 'The regulator input range is 8 to 20 volts.', 'sha256': 'f'*64, 'version_id': 7}
    profiles = [ModelProfile(name, 1, 4096, 'test', 1) for name in ('first', 'second')]
    result = compact_chat.generate({'prompt': 'What is the permitted input range?'}, tmp_path/'memory.sqlite3',
                                   profiles, 'general', ModelEvidence(tmp_path/'quality.sqlite3'), source_evidence=[source])
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        rows = memory.history(result['session'], include_metadata=True)
    assert len(rows) == 2, 'Failed attempt must not create an extra conversation exchange'
    saved = rows[-1]['details']
    assert [a['status'] for a in saved['metrics']['attempts']] == ['failed', 'completed']
    assert saved['metrics'] == result['details']['metrics']
    assert saved['grounding'] == result['details']['grounding']
    assert saved['routing']['attempts'] == 2 and saved['model'] == 'second'
