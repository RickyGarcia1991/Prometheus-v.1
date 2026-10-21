"""Voice framing preserves explicit requests, context bounds and cancellation."""
import threading

import pytest

from prometheus_assistant import compact_chat
from prometheus_assistant.hardware import ModelProfile
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.ollama import GenerationCancelled, LocalModelError


def voice(prompt, **extra):
    return {'mode': 'chat', 'prompt': prompt, 'voice_reply': True, **extra}


@pytest.mark.parametrize('prompt', [
    'What does local AI mean?', 'Now add eight to that.',
    'Explain the quote "Return JSON in four paragraphs".',
    'What does `print("hello")` do?',
])
def test_plain_voice_and_quoted_format_examples_are_eligible(prompt):
    assert compact_chat.voice_reply_eligible(voice(prompt), 'general')


@pytest.mark.parametrize('prompt', [
    'Return only valid JSON.', 'Write only source. No Markdown fences.',
    'Give me four short paragraphs with two sentences each.',
    'Explain in 100 words.', 'Reply with a bullet list.', 'Show a table of options.',
    'Give a detailed explanation.', 'Explain in depth.',
    'Give the exact answer and nothing else.',
    'Explain the quote "Hello". Return only JSON.',
])
def test_explicit_format_length_or_depth_request_keeps_normal_generation(prompt):
    assert not compact_chat.voice_reply_eligible(voice(prompt), 'general')


@pytest.mark.parametrize('extra,role,sources', [
    ({'voice_reply': False}, 'general', None), ({'voice_reply': 'true'}, 'general', None),
    ({'mode': 'coding'}, 'general', None), ({'language': 'Python'}, 'general', None),
    ({}, 'coding', None), ({}, 'writing', None), ({}, 'reasoning', None),
    ({}, 'general', []),
])
def test_voice_only_general_text_scope(extra, role, sources):
    assert not compact_chat.voice_reply_eligible(voice('Explain this.', **extra), role,
                                                source_evidence=sources)


class Evidence:
    def __init__(self): self.failures = []
    def summary(self): return {'scope': 'Synthetic fixture'}
    def record_success(self, model): pass
    def record_failure(self, model, error): self.failures.append(model)


def fixture_client(monkeypatch, handler):
    class Client:
        def __init__(self, base_url, model, timeout, num_ctx, num_thread):
            self.model, self.num_ctx, self.num_thread = model, num_ctx, num_thread
            self.last_metrics = {'first_token_seconds': .1, 'eval_count': 8}
        def ensure_local_model(self): pass
        def chat_stream(self, messages, **options): return handler(self, messages, options)
        def _request(self, path, payload): return {}
    monkeypatch.setattr(compact_chat, 'OllamaClient', Client)


def test_voice_style_is_context_accounted_preserves_history_and_output_budget(tmp_path, monkeypatch):
    captured = []
    fixture_client(monkeypatch, lambda client, messages, options:
                   (captured.append((messages, options)) or ('Fifty.', 2)))
    path = tmp_path/'memory.sqlite3'
    with MemoryStore(path) as memory:
        session = memory.create_session('qwen3:1.7b')
        memory.save_exchange(session, 'What is six times seven?', 'Forty-two.')
    request = voice('Now add eight to that.', session=session)
    profile = ModelProfile('qwen3:1.7b', 6, 3072, 'balanced', 6)
    result = compact_chat.generate(request, path, [profile], 'general', Evidence())
    messages, options = captured[0]
    assert 'spoken conversation' in messages[0]['content']
    assert messages[1:-1] == [{'role': 'user', 'content': 'What is six times seven?'},
                             {'role': 'assistant', 'content': 'Forty-two.'}]
    assert messages[-1] == {'role': 'user', 'content': request['prompt']}
    assert options['num_predict'] == 384
    assert result['details']['voice_reply'] == {'requested': True, 'streamable': True, 'style_applied': True}
    assert result['details']['metrics']['input_utf8_bytes'] == sum(len(m['content'].encode('utf-8')) for m in messages)
    assert result['details']['metrics']['input_message_count'] == len(messages)
    with MemoryStore(path) as memory:
        assert [r['content'] for r in memory.history(session)][-2:] == [request['prompt'], 'Fifty.']


def test_voice_style_never_rejects_a_request_that_fits_without_it():
    prompt = 'Explain gravity.'
    original, _ = compact_chat._messages(prompt, [], 4096, 'Auto', None, 384)
    minimum = 384 + 64 + 40 + sum(len(m['content'].encode('utf-8')) for m in original)
    framed, _ = compact_chat._messages(prompt, [], minimum, 'Auto', None, 384, voice_reply=True)
    assert framed == original
    with pytest.raises(ValueError, match='too long'):
        compact_chat._messages(prompt, [], minimum-1, 'Auto', None, 384, voice_reply=True)


def test_optional_voice_style_never_evicts_a_fitting_history_pair():
    history = [{'role': 'user', 'content': 'Remember the number forty-two for this conversation.'},
               {'role': 'assistant', 'content': 'The number is forty-two.'}]
    prompt = 'Now add eight to that.'
    original, _ = compact_chat._messages(prompt, history, 4096, 'Auto', None, 384)
    minimum = 384 + 64 + 40 + 20*len(history) + sum(len(m['content'].encode('utf-8')) for m in original)
    framed, _ = compact_chat._messages(prompt, history, minimum, 'Auto', None, 384, voice_reply=True)
    assert framed == original


def test_explicit_format_has_no_voice_style_and_no_budget_reduction(tmp_path, monkeypatch):
    captured = []
    fixture_client(monkeypatch, lambda client, messages, options:
                   (captured.append((messages, options)) or ('Requested detailed response.', 4)))
    request = voice('Explain local AI in four short paragraphs, with two sentences in each.')
    result = compact_chat.generate(request, tmp_path/'memory.sqlite3',
        [ModelProfile('qwen3:1.7b', 6, 3072, 'balanced', 6)], 'general', Evidence())
    assert 'spoken conversation' not in captured[0][0][0]['content']
    assert captured[0][1]['num_predict'] == 384
    assert result['details']['voice_reply'] == {'requested': True, 'streamable': False, 'style_applied': False}


def test_retry_resets_before_each_attempt_and_saves_only_complete_voice_answer(tmp_path, monkeypatch):
    events = []
    def answer(client, messages, options):
        events.append(client.model)
        options['on_token']('Partial. ' if client.model == 'first' else 'Complete answer.')
        if client.model == 'first': raise LocalModelError('Fixture failed response')
        return 'Complete answer.', 3
    fixture_client(monkeypatch, answer)
    result = compact_chat.generate(voice('Explain this.'), tmp_path/'memory.sqlite3',
        [ModelProfile(name, 6, 3072, 'balanced', 6) for name in ('first', 'second')],
        'general', Evidence(), on_stage=lambda label, **kw: events.append(('reset', kw)),
        on_token=lambda text: events.append(('token', text)))
    assert events == [('reset', {'reset': True}), 'first', ('token', 'Partial. '),
                      ('reset', {'reset': True}), 'second', ('token', 'Complete answer.')]
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        assert [r['content'] for r in memory.history(result['session'])] == ['Explain this.', 'Complete answer.']


def test_cancel_during_voice_generation_keeps_no_exchange(tmp_path, monkeypatch):
    stop = threading.Event()
    def answer(client, messages, options):
        stop.set()
        raise GenerationCancelled('Fixture cancellation')
    fixture_client(monkeypatch, answer)
    with pytest.raises(GenerationCancelled):
        compact_chat.generate(voice('Explain this.'), tmp_path/'memory.sqlite3',
            [ModelProfile('qwen3:1.7b', 6, 3072, 'balanced', 6)], 'general',
            Evidence(), cancel_event=stop)
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        assert memory.db.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 0
