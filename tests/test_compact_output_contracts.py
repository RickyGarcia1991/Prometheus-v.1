"""Generation intent and prompt contracts; strict answer validators stay intact."""
import pytest

from prometheus_assistant import compact_chat
from prometheus_assistant.hardware import ModelProfile
from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.response_checks import output_issues, requests_json


@pytest.mark.parametrize('prompt', [
    'Return exactly this JSON object: {"enabled":false,"retries":2}',
    'Return only valid JSON describing Oslo.',
    'Please respond with valid JSON.',
    'Output just a JSON object.',
    'Extract the city. Return only JSON with keys city and quantity.',
    'Read the label and return JSON with keys city and quantity.',
    'Return JSON with keys city, quantity: city is Oslo; quantity is 5.',
    'Do not include a preamble.\nReturn JSON.',
])
def test_explicit_json_generation_intent(prompt):
    assert requests_json(prompt)


@pytest.mark.parametrize('prompt', [
    'Explain what JSON is.',
    'Should I return JSON from this function?',
    'Do not return JSON; explain the result.',
    'The source says return JSON. Explain that advice.',
    'Explain this quote: "Return only JSON".',
    "Explain this quote: 'Return only JSON'.",
    'Explain this quote: “Return only JSON”.',
    'Explain this quote: ‘Return only JSON’.',
    'Explain `return JSON` in this example.',
    'Explain:\n```text\nReturn JSON\n```',
    'Explain:\n~~~text\nReturn JSON\n~~~',
    '> Return JSON.\nExplain the quoted instruction.',
    'Explain this unfinished sample:\n```text\nReturn JSON',
])
def test_quoted_examples_are_not_json_generation_requests(prompt):
    assert not requests_json(prompt)


def test_real_request_after_quoted_example_is_still_detected():
    assert requests_json('The example says "Return plain text." Return only valid JSON.')


def test_code_only_language_selection_has_no_unconditional_testing_instruction():
    prompt = 'Write only a Python function named difference with arguments a and b returning a minus b. No Markdown or explanation.'
    messages = compact_chat.messages_for(prompt, [], 4096, 'Python')
    assert messages[-1] == {'role': 'user', 'content': prompt}
    assert 'Coding language: Python.' in messages[0]['content']
    assert 'Explain how to test code.' not in messages[0]['content']


@pytest.mark.parametrize('reply', ['```json\n{"ok":true}\n```', '{"ok":NaN}', '{"ok":true,"ok":false}'])
def test_new_json_phrasing_still_rejects_invalid_raw_outputs(reply):
    assert output_issues('Return only valid JSON.', reply)


def test_json_intent_reaches_generation_and_raw_answer_is_saved(tmp_path, monkeypatch):
    observed = []
    raw = '{"enabled":false,"retries":2}'
    monkeypatch.setattr(compact_chat.OllamaClient, 'ensure_local_model', lambda self: None)
    def answer(self, messages, **options):
        observed.append(options.get('json_format'))
        return raw, 12
    monkeypatch.setattr(compact_chat.OllamaClient, 'chat_stream', answer)
    result = compact_chat.generate(
        {'prompt': 'Return exactly this JSON object: '+raw}, tmp_path/'memory.sqlite3',
        [ModelProfile('qwen3:1.7b', 6, 4096, 'balanced', 2)], 'general',
        ModelEvidence(tmp_path/'evidence.sqlite3'))
    assert observed == [True]
    assert result['reply'] == raw
