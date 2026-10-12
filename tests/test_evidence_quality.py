"""Regression cases for useful evidence, bounded prompts and honest absence."""
import json
from prometheus_assistant.document_library import DocumentLibrary
from prometheus_assistant.compact_chat import grounded_messages


def test_long_question_keeps_decisive_terms_at_the_end(tmp_path):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('overview.md', b'Board design supply controller sensors block diagram computer.')
    library.import_bytes('clock.md', b'Oscillator crystal startup capacitance must match the datasheet.')
    query = ('Help design a small board powered from a nominal supply with a microcontroller. '
             'I have not selected sensors or a controller. Give me a draft block diagram in words, '
             'the missing requirements and provisional structure, especially oscillator crystal startup capacitance.')
    result = library.search(query, semantic=False)
    assert 'capacitance' in result['search_terms']
    assert 'clock.md' in [row['title'] for row in result['results']]


def test_exact_missing_reference_designator_is_not_generic_evidence(tmp_path):
    library = DocumentLibrary(tmp_path)
    library.import_bytes('guide.md', b'Motherboard resistor repair requires a schematic and fault measurements.')
    result = library.search('Tell me the exact value of resistor R999 on my motherboard and guarantee a repair.', semantic=False)
    assert not result['results']
    assert 'R999' in result['note']
    assert 'measurements' in result['note']


def test_packing_retains_relevant_tail_and_exact_provenance():
    text = ('Release history and document publication notes.\n' * 20
            + '\n## Supply enable\nThe controller enable signal must be high before the output rail can start.\n')
    source = {'id':'S1', 'title':'Power manual', 'location':'https://example.org/revision/page',
              'excerpt':text, 'sha256':'a'*64, 'version_id':9, 'source_date':'2026-01-01'}
    messages, selected = grounded_messages('Why can the output rail not start, and what enable signal is required?',
                                           [], 1800, [source], output_tokens=384)
    assert 'enable signal must be high' in selected[0]['excerpt']
    assert selected[0]['excerpt'] in text
    assert selected[0]['excerpt_truncated']
    assert selected[0]['sha256'] == 'a'*64 and selected[0]['version_id'] == 9
    assert sum(len(m['content'].encode('utf-8'))+20 for m in messages)+384 <= 1800


def test_full_provenance_is_retained_without_spending_model_context_on_hashes():
    source = {'id':'S1', 'title':'Guide', 'location':'https://example.org/a/very/long/location',
              'excerpt':'The maximum clock frequency is 8 MHz.', 'sha256':'b'*64, 'version_id':3}
    messages, selected = grounded_messages('What is the maximum clock frequency?', [], 4096, [source])
    assert selected[0]['sha256'] == 'b'*64 and selected[0]['location'] == source['location']
    assert 'b'*64 not in json.dumps(messages)


def test_complex_answer_uses_one_matching_budget_and_persists_provenance(tmp_path, monkeypatch):
    from prometheus_assistant import compact_chat
    from prometheus_assistant.hardware import ModelProfile
    from prometheus_assistant.model_quality import ModelEvidence
    from prometheus_assistant.memory import MemoryStore
    profile = ModelProfile('test:local', 1, 4096, 'test', 1)
    received = []
    monkeypatch.setattr(compact_chat.OllamaClient, 'ensure_local_model', lambda self: None)
    def chat(self, messages, **kwargs):
        received.append(kwargs['num_predict'])
        assert sum(len(m['content'].encode('utf-8'))+20 for m in messages)+kwargs['num_predict'] <= self.num_ctx
        return 'A provisional plan [S1].', 6
    monkeypatch.setattr(compact_chat.OllamaClient, 'chat_stream', chat)
    result = compact_chat.generate({'prompt':'Draft a design plan.'}, tmp_path/'memory.sqlite3', [profile],
        'general', ModelEvidence(tmp_path/'quality.sqlite3'), source_evidence=[
            {'id':'S1','title':'Guide','location':'line 1','excerpt':'Review requirements before selecting parts.'}])
    assert received == [896]
    assert compact_chat.response_budget('Hello', profile) == 384
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        saved = memory.history(result['session'], include_metadata=True)[-1]
        assert saved['details']['grounding'] == result['details']['grounding']
        assert saved['details']['metrics']['output_tokens'] == 896
