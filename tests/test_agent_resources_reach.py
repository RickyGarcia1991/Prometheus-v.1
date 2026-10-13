import threading

import pytest

from prometheus_assistant import public_reach, ui_server
from prometheus_assistant.ollama import GenerationCancelled


@pytest.mark.parametrize('query', ['', 'all', 'OpenViking'])
def test_catalogue_read_works_offline_without_network_or_model(query, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Local catalogue must not fetch, infer, or start processes')
    monkeypatch.setattr(public_reach, 'fetch_public_bytes', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr('prometheus_assistant.ollama.OllamaClient._request', forbidden)
    result = public_reach.read_public('agent-resources', query)
    assert result['offline'] is True and result['network_used'] is False
    assert result['generated_answer'] is False and result['code_executed'] is False
    assert result['action'] == 'agent-resources'
    assert 0 < len(result['results']) <= 10
    assert result['reply'] and result['results'][0]['source_url'].startswith('https://')
    assert 'endpoint' not in result and 'retrieved_utc' not in result


@pytest.mark.parametrize('query', [None, 1, 'a'*241, 'bad\nquery'])
def test_catalogue_invalid_query_is_rejected_before_lookup(query, monkeypatch):
    monkeypatch.setattr('prometheus_assistant.agent_resources.search_resources',
                        lambda *args, **kwargs: pytest.fail('Invalid input reached lookup'))
    with pytest.raises(ValueError):
        public_reach.read_public('agent-resources', query)


def test_catalogue_cancel_and_capability_denial_are_preserved(monkeypatch):
    stop = threading.Event()
    stop.set()
    with pytest.raises(GenerationCancelled):
        public_reach.read_public('agent-resources', 'all', cancel_event=stop)
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', 'reach.read')
    with pytest.raises(ValueError, match='denied'):
        public_reach.read_public('agent-resources', 'all')


def test_interface_catalogue_result_is_labelled_local_and_retains_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(public_reach, 'fetch_public_bytes',
                        lambda *args, **kwargs: pytest.fail('Unexpected fetch'))
    app = ui_server.Interface(tmp_path/'memory.sqlite3')
    try:
        request = ui_server.validate_job({'mode': 'reach', 'reach_action': 'agent-resources',
                                         'prompt': 'browser-use'})
        result = app.execute(request)
        assert result['label'] == 'Agent resources · local catalogue'
        assert result['details']['network_used'] is False
        assert result['details']['offline'] is True
        assert result['details']['results'][0]['availability'] == 'requires-setup'
        assert 'adapter not installed' in result['reply']
    finally:
        app.close()
