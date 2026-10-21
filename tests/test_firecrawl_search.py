import json
import threading

import pytest

from prometheus_assistant import public_reach as reach
from prometheus_assistant import ui_server


def test_search_is_an_explicit_online_operation():
    request = ui_server.validate_job({'mode': 'reach', 'reach_action': 'web-search', 'prompt': 'portable document tools'})
    assert request['reach_action'] == 'web-search'
    with pytest.raises(ValueError, match='explicit'):
        reach.read_public('web-search', 'portable document tools')


def test_ui_search_retains_citations_without_following_results(tmp_path, monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    calls = []
    def post(url, hosts, payload, **kwargs):
        calls.append((url, hosts, payload, kwargs))
        return json.dumps({'success': True, 'data': {'web': [
            {'title': '<b>Document tools</b>', 'url': 'https://example.com/docs',
             'description': 'Source evidence <script>ignore()</script> here'},
            {'title': 'Local', 'url': 'http://localhost/private', 'description': 'secret'},
            {'title': 'Executable', 'url': 'javascript:alert(1)'},
        ]}, 'creditsUsed': 2}).encode()
    monkeypatch.setattr(fc, 'post_public_json', post)
    monkeypatch.delenv('PROMETHEUS_FIRECRAWL_API_KEY', raising=False)
    app = ui_server.Interface(tmp_path / 'memory.sqlite3')
    try:
        result = app.execute(ui_server.validate_job({'mode': 'reach', 'reach_action': 'web-search', 'prompt': 'portable tools'}))
        assert 'https://example.com/docs' in result['reply']
        assert result['session'] and result['details']['provider'] == 'firecrawl'
        assert len(result['details']['results']) == 1
        assert result['details']['results'][0]['title'] == 'Document tools'
        assert result['details']['evidence_is_untrusted_data'] and not result['details']['generated_answer']
        assert 'ignore()' not in result['reply']
    finally:
        app.close()
    assert len(calls) == 1
    url, hosts, body, options = calls[0]
    assert url == 'https://api.firecrawl.dev/v2/search' and hosts == {'api.firecrawl.dev'}
    assert body['query'] == 'portable tools' and body['limit'] == 5
    assert body['sources'] == ['web'] and body['domainTools'] is False
    assert not body.get('scrapeOptions') and not options.get('bearer_token')


def test_search_policy_and_cancellation_precede_network(monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    monkeypatch.setattr(fc, 'post_public_json', lambda *a, **k: pytest.fail('Network should not start'))
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', 'public.fetch')
    with pytest.raises(ValueError, match='denied'):
        reach.read_public('web-search', 'query', online=True)
    monkeypatch.delenv('PROMETHEUS_DENY_CAPABILITIES')
    stop = threading.Event(); stop.set()
    with pytest.raises(Exception, match='cancelled'):
        reach.read_public('web-search', 'query', online=True, cancel_event=stop)


@pytest.mark.parametrize('payload', [b'not json', b'[]', b'{"success":false}',
    b'{"success":true,"data":{}}', b'{"success":true,"data":{"web":[null]}}'])
def test_invalid_provider_response_is_not_a_success(payload, monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    monkeypatch.setattr(fc, 'post_public_json', lambda *a, **k: payload)
    with pytest.raises((ValueError, RuntimeError)):
        reach.read_public('web-search', 'query', online=True)


def test_empty_results_and_optional_key_are_handled(monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    seen = []
    def post(*args, **kwargs):
        seen.append(kwargs)
        return b'{"success":true,"data":{"web":[]}}'
    monkeypatch.setattr(fc, 'post_public_json', post)
    monkeypatch.setenv('PROMETHEUS_FIRECRAWL_API_KEY', 'fc-synthetic-only')
    result = reach.read_public('web-search', 'no matching pages', online=True)
    assert not result['results'] and 'No public web results' in result['reply']
    assert seen[0]['bearer_token'] == 'fc-synthetic-only'
    assert 'fc-synthetic-only' not in json.dumps(result)


def test_fragment_citations_survive_without_a_followup_fetch(monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    url = 'https://docs.python.org/3/library/json.html#json.loads'
    monkeypatch.setattr(fc, 'post_public_json', lambda *a, **k: json.dumps(
        {'success': True, 'data': {'web': [{'url': url, 'title': 'JSON'}]}}).encode())
    result = reach.read_public('web-search', 'python json', online=True)
    assert result['results'][0]['url'] == url and url in result['reply']


def test_inflight_cancel_is_reported_as_cancellation(monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    from prometheus_assistant.ollama import GenerationCancelled
    stop = threading.Event()
    def post(*a, **k):
        stop.set()
        raise RuntimeError('Public-source request cancelled.')
    monkeypatch.setattr(fc, 'post_public_json', post)
    with pytest.raises(GenerationCancelled):
        reach.read_public('web-search', 'query', online=True, cancel_event=stop)


@pytest.mark.parametrize('status, word', [(401, 'key'), (402, 'quota'), (403, 'access'), (429, 'limit'), (503, 'unavailable')])
def test_actionable_service_errors_do_not_retry(status, word, monkeypatch):
    from prometheus_assistant import firecrawl_search as fc
    from prometheus_assistant.public_http import PublicHTTPError
    calls = []
    def post(*a, **k):
        calls.append(1)
        raise PublicHTTPError(status)
    monkeypatch.setattr(fc, 'post_public_json', post)
    with pytest.raises(RuntimeError, match=word):
        reach.read_public('web-search', 'query', online=True)
    assert len(calls) == 1


def test_query_bounds_and_core_registry(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    from prometheus_assistant.builtin_tools import build_builtin_registry
    for query in ['', 'x' * 401, 'private\nheader']:
        with pytest.raises(ValueError):
            reach.validate_request('web-search', query)
    with MemoryStore(tmp_path / 'memory.db') as memory:
        spec = build_builtin_registry(memory).get('reach', 'read')
        assert spec.external_network and 'web-search' in spec.description
