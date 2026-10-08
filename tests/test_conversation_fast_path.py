import pytest
from prometheus_assistant.agent import _safe_conversation_fast_path

@pytest.mark.parametrize('prompt', [
    'Hello!', 'Explain photosynthesis.', 'Define gravity.',
    'Give me three tips for studying.', 'Write a short poem about stars.',
])
def test_fast_safe(prompt):
    assert _safe_conversation_fast_path(prompt)

@pytest.mark.parametrize('prompt', [
    'Remember my birthday', 'Recall our last conversation',
    'Search the internet for photosynthesis', 'What is the current system status?',
    'Explain my computer RAM', 'Describe the files on this drive',
    'Run a system diagnostic', 'Write a file about stars',
    'Explain the latest research',
])
def test_never_skip_evidence_or_actions(prompt):
    assert not _safe_conversation_fast_path(prompt)

def test_online_or_required_request_never_skipped():
    assert not _safe_conversation_fast_path('Explain gravity.', online_enabled=True)
    assert not _safe_conversation_fast_path('Explain gravity.', required_requests=(object(),))
