"""Saved evidence remains inspectable without entering model history or credentials."""
import sqlite3

import pytest

from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.recovery import backup_memory, restore_memory


def evidence():
    return {
        'model': 'test-helper', 'generated_answer': True,
        'grounding': {
            'sources': [{'id': 'S2', 'title': 'Pinout <script>',
                         'location': 'https://example.org/pinned/manual#pin2 | page 3',
                         'excerpt': 'Pin 2 is ground. Ω', 'sha256': 'a' * 64,
                         'version_id': 7, 'is_latest': False, 'excerpt_truncated': False}],
            'recognized_citations': ['S2'], 'unrecognized_citations': ['S99'],
            'claim_verification': 'not performed'},
        'metrics': {'seconds': 1.25, 'first_token_seconds': .2, 'eval_count': 12,
                    'model': 'test-helper', 'context': 4096, 'load_seconds': .1},
        'routing': {'role': 'general', 'automatic': True, 'reason': 'Measured fit.', 'attempts': 1}}


def test_saved_evidence_survives_reopen_without_changing_model_history(tmp_path):
    path = tmp_path/'memory.db'
    details = evidence()
    with MemoryStore(path) as store:
        sid = store.create_session('old-model')
        store.save_exchange(sid, 'Question', 'Answer [S2]', label='Current model', details=details)
        plain = store.history(sid)
        assert all(set(row) == {'role', 'content', 'created_at'} for row in plain)
    with MemoryStore(path) as store:
        rows = store.history(sid, include_metadata=True)
        assert rows[0] == plain[0]
        assert rows[1] == {**plain[1], 'label': 'Current model', 'details': details}
        assert store.history(sid, limit=1, include_metadata=True) == rows[-1:]
        assert store.history(sid) == plain
        assert store.db.execute('PRAGMA user_version').fetchone()[0] == 2


def test_existing_v2_history_gains_optional_table_without_rewriting_rows(tmp_path):
    path = tmp_path/'legacy.db'
    with MemoryStore(path) as store:
        sid = store.create_session('legacy')
        store.save_exchange(sid, 'Earlier question', 'Earlier answer [S1]')
        store.remember('fact', 'owner', 'Example', source_type='user_statement', source_ref='test')
        original = store.history(sid)
        store.db.execute('DROP TABLE IF EXISTS turn_metadata')
        store.db.commit()
    with MemoryStore(path) as store:
        assert store.history(sid, include_metadata=True) == original
        store.save_exchange(sid, 'Next question', 'Next answer', label='New label', details={'generated_answer': False})
        assert store.history(sid, include_metadata=True)[:2] == original
        assert store.knowledge()[0]['value'] == 'Example'
        assert store.db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert store.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_metadata_uses_bounded_allowlist_at_every_level(tmp_path):
    details = evidence()
    secret = 'fixture-private-token-image-or-control-key'
    details.update({'token': secret, 'home_key': secret, 'content': secret, 'request': {'prompt': secret}})
    details['grounding']['token'] = secret
    details['grounding']['sources'][0].update({'token': secret, 'images': [secret]})
    details['metrics']['authorization'] = secret
    details['routing']['request'] = {'content': secret}
    with MemoryStore(tmp_path/'memory.db') as store:
        sid = store.create_session('test')
        store.save_exchange(sid, 'Question', 'Answer', details=details)
        saved = store.history(sid, include_metadata=True)[1]['details']
        assert saved == evidence()
        assert secret not in store.db.execute('SELECT details_json FROM turn_metadata').fetchone()[0]


@pytest.mark.parametrize('kwargs', [
    {'label': 'x' * 241},
    {'details': {'sources': [{'excerpt': 'x' * 16385}]}},
    {'details': {'sources': [{'excerpt': 'ok'}] * 6}},
    {'details': {'metrics': {'seconds': float('nan')}}},
    {'details': {'metrics': {'seconds': 10 ** 400}}},
    {'details': {'grounding': {'sources': [{'excerpt': '😀' * 16000}] * 5}}},
])
def test_invalid_or_oversized_metadata_cannot_partially_save_exchange(tmp_path, kwargs):
    with MemoryStore(tmp_path/'memory.db') as store:
        sid = store.create_session('test')
        with pytest.raises(ValueError, match='metadata'):
            store.save_exchange(sid, 'Question', 'Answer', **kwargs)
        assert store.history(sid) == []


def test_metadata_database_failure_rolls_back_both_messages(tmp_path):
    with MemoryStore(tmp_path/'memory.db') as store:
        sid = store.create_session('test')
        store.db.execute("CREATE TRIGGER reject_metadata BEFORE INSERT ON turn_metadata BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='test failure'):
            store.save_exchange(sid, 'Question', 'Answer', label='Label')
        assert store.history(sid) == []


def test_backup_restore_preserves_metadata_and_unmodified_legacy_messages(tmp_path):
    source, backup, restored = [tmp_path/name for name in ('source.db', 'backup.db', 'restored.db')]
    with MemoryStore(source) as store:
        sid = store.create_session('test')
        store.save_exchange(sid, 'Old question', 'Old answer')
        store.save_exchange(sid, 'Question', 'Answer', label='Evidence helper', details=evidence())
        expected = store.history(sid, include_metadata=True)
    assert backup_memory(source, backup)['schema_version'] == 2
    restore_memory(backup, restored)
    with MemoryStore(restored) as store:
        assert store.history(sid, include_metadata=True) == expected


def test_corrupt_optional_metadata_never_hides_saved_message(tmp_path):
    with MemoryStore(tmp_path/'memory.db') as store:
        sid = store.create_session('test')
        store.save_exchange(sid, 'Question', 'Answer', label='Saved helper', details=evidence())
        store.db.execute("UPDATE turn_metadata SET details_json = '{broken'")
        row = store.history(sid, include_metadata=True)[-1]
        assert row['content'] == 'Answer' and row['label'] == 'Saved helper'
        assert 'details' not in row


def test_excerpt_only_source_metadata_is_retained(tmp_path):
    details = {'sources': evidence()['grounding']['sources'], 'generated_answer': False,
               'source': 'documents', 'retrieval': 'keyword', 'evidence_is_untrusted_data': True,
               'reason': 'No eligible local model.', 'reply': 'Do not duplicate the reply.'}
    with MemoryStore(tmp_path/'memory.db') as store:
        sid = store.create_session('references')
        store.save_exchange(sid, 'Question', 'Source excerpt', details=details)
        saved = store.history(sid, include_metadata=True)[-1]['details']
        assert saved == {k: v for k, v in details.items() if k != 'reply'}
