"""General evidence coverage and source-following contracts, not hardware answer keys."""
from prometheus_assistant.compact_chat import grounded_messages


def test_later_independent_subjects_receive_useful_context():
    subjects = ('installation', 'rollback', 'upgrades', 'logging', 'monitoring')
    sources = []
    for i, subject in enumerate(subjects):
        text = ('General product manual background and revision notes.\n' * 30
                + f'\n## {subject.title()}\n'
                + f'The {subject} procedure requires a verified prerequisite before the next action.\n' * 8)
        sources.append({'id':f'S{i+1}', 'title':subject, 'location':f'manual-{i}', 'excerpt':text})
    messages, supplied = grounded_messages('Compare installation, rollback, upgrades, logging and monitoring.',
                                           [], 8192, sources, output_tokens=896)
    assert {r['title'] for r in supplied} == set(subjects)
    assert all(len(r['excerpt']) >= 300 for r in supplied)
    assert all(r['excerpt'] in sources[int(r['id'][1:])-1]['excerpt'] for r in supplied)
    assert sum(len(m['content'].encode('utf-8'))+20 for m in messages)+896 <= 8192


def test_short_source_leaves_room_for_longer_procedure():
    short = {'id':'S1', 'title':'Scope', 'location':'intro', 'excerpt':'This document covers the upgrade procedure.'}
    long = {'id':'S2', 'title':'Procedure', 'location':'steps',
            'excerpt':'Upgrade prerequisites: verify the backup, then check the maintenance window.\n'*15}
    _, supplied = grounded_messages('Explain the upgrade procedure.', [], 4096, [short,long])
    assert supplied[0]['excerpt'] == short['excerpt']
    assert len(supplied[1]['excerpt']) >= 600


def test_larger_context_retains_complete_short_manual_sections():
    sources = [{'id':f'S{i+1}', 'title':f'Procedure {i}', 'location':f'page {i}',
                'excerpt':f'Section {i}: '+('Prerequisites, instructions and acceptance conditions.\n'*36)}
               for i in range(5)]
    messages, supplied = grounded_messages('Compare the procedures and their conditions.', [],
                                           16384, sources, output_tokens=1152)
    assert [r['excerpt'] for r in supplied] == [r['excerpt'] for r in sources]
    assert sum(len(m['content'].encode('utf-8'))+20 for m in messages)+1152 <= 16384


def test_audit_metadata_does_not_crowd_out_reference_text():
    basic = [{'id':f'S{i+1}', 'title':f'Maintenance {i}', 'location':'chapter',
              'excerpt':'Check the documented prerequisite before continuing.\n'*20}
             for i in range(5)]
    audited = [{**row, 'location':'https://example.test/manual/revisions/'+'v'*300,
                'source_date':'2026-01-01', 'imported_at':'2026-02-01T00:00:00Z',
                'version_id':91, 'sha256':'a'*64, 'is_latest':True} for row in basic]
    before, short_sources = grounded_messages('Explain the maintenance prerequisites.', [], 4096, basic)
    after, full_sources = grounded_messages('Explain the maintenance prerequisites.', [], 4096, audited)
    assert before == after
    assert [r['excerpt'] for r in short_sources] == [r['excerpt'] for r in full_sources]
    # Audit records remain available to the UI and saved conversation.
    assert all(r['sha256'] == 'a'*64 and r['version_id'] == 91 for r in full_sources)
    assert [r['location'] for r in full_sources] == [r['location'] for r in audited]

def test_failed_attempt_metrics_remain_visible_without_saving_partial_answer(tmp_path, monkeypatch):
    from prometheus_assistant import compact_chat
    from prometheus_assistant.hardware import ModelProfile
    from prometheus_assistant.model_quality import ModelEvidence
    from prometheus_assistant.memory import MemoryStore
    from prometheus_assistant.ollama import LocalModelError
    profiles = [ModelProfile(name, 1, 4096, 'test', 1) for name in ('first:local','second:local')]
    monkeypatch.setattr(compact_chat.OllamaClient, 'ensure_local_model', lambda self: None)
    def chat(self, messages, **kwargs):
        if self.model == 'first:local':
            self.last_metrics = {'load_seconds': 7.5, 'eval_count': 896}
            raise LocalModelError('The answer reached its output limit; partial text was not saved.')
        self.last_metrics = {'load_seconds': .1, 'eval_count': 4}
        return 'Completed answer.', 4
    monkeypatch.setattr(compact_chat.OllamaClient, 'chat_stream', chat)
    result = compact_chat.generate({'prompt':'Draft a short plan.'}, tmp_path/'memory.sqlite3', profiles,
                                  'general', ModelEvidence(tmp_path/'quality.sqlite3'))
    attempts = result['details']['metrics']['attempts']
    assert attempts[0]['status'] == 'failed' and attempts[0]['metrics']['load_seconds'] == 7.5
    assert result['details']['metrics']['load_seconds'] == .1
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        turns = memory.history(result['session'], include_metadata=True)
        assert [row['content'] for row in turns] == ['Draft a short plan.', 'Completed answer.']
        assert turns[-1]['details']['metrics']['attempts'][0]['status'] == 'failed'
