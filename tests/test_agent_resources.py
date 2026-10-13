import json

import pytest

from prometheus_assistant import agent_resources


def test_exact_lookup_is_case_insensitive_and_precedes_other_memory_results():
    result = agent_resources.search_resources('aGeNtMeMoRy')
    assert result['results'][0]['id'] == 'agentmemory-rohitg00'
    assert result['results'][0]['publisher'] == 'Rohit Ghumare / rohitg00'
    assert result['results'][0]['source_url'] == 'https://github.com/rohitg00/agentmemory'


def test_partial_lookup_normalizes_punctuation_and_matches_multiple_terms():
    assert agent_resources.search_resources('BROWSER USE')['results'][0]['id'] == 'browser-use'
    assert agent_resources.search_resources('Viking')['results'][0]['id'] == 'openviking'
    assert agent_resources.search_resources('coding memory')['results'][0]['id'] == 'agentmemory-rohitg00'
    assert agent_resources.search_resources('volcengine/OpenViking')['results'][0]['id'] == 'openviking'


def test_reference_discovery_does_not_contact_network_or_launch_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Catalogue lookup may not perform network or process actions')
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr('subprocess.Popen', forbidden)
    result = agent_resources.search_resources('')
    assert len(result['results']) >= 5
    assert result['network_used'] is False
    assert len(result['results']) <= 10
    assert 'no network requests or installation' in result['note']


def test_catalogue_does_not_claim_uninstalled_services_are_working():
    names = ('browser-use', 'agentmemory-rohitg00', 'openviking', 'bolt-new', 'agent-reach')
    rows = {name: agent_resources.search_resources(name)['results'][0] for name in names}
    for name in ('openviking',):
        assert rows[name]['availability'] == 'requires-setup'
        assert ('not installed' in rows[name]['limitations']
                or 'No Prometheus' in rows[name]['limitations'])
    for name in ('browser-use', 'agentmemory-rohitg00'):
        assert rows[name]['availability'] == 'reviewed-adapter'
        assert 'Dev20 includes' in rows[name]['limitations']
        assert 'Integrations' in rows[name]['next_step']
    assert rows['bolt-new']['availability'] == 'reference'
    assert rows['agent-reach']['availability'] == 'existing-public-adapter'
    assert 'complete upstream package' in rows['agent-reach']['limitations']
    assert 'not installed' in rows['agent-reach']['limitations']
    assert all(row['source_url'].startswith('https://github.com/') for row in rows.values())
    assert all(row['source_commit'] is None for row in rows.values())


def test_lookup_returns_fresh_records_and_unknown_query_is_explicit():
    result = agent_resources.search_resources('browser-use')
    result['results'][0]['name'] = 'mutated'
    assert agent_resources.search_resources('browser-use')['results'][0]['name'] == 'Browser Use'
    unknown = agent_resources.search_resources('unknown synthetic resource 812')
    assert unknown['results'] == []
    assert unknown['total_matches'] == 0
    assert 'No catalogue entry' in agent_resources.render_resources(unknown)


def test_result_limit_is_bounded_even_for_a_larger_catalogue(tmp_path, monkeypatch):
    data = json.loads(agent_resources.CATALOG_PATH.read_text(encoding='utf-8'))
    template = data['resources'][0]
    data['resources'] = [dict(template, id=f'fixture-{index}', name=f'Fixture {index}') for index in range(25)]
    catalog = tmp_path / 'resources.json'
    catalog.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(agent_resources, 'CATALOG_PATH', catalog)
    result = agent_resources.search_resources('Fixture')
    assert result['total_matches'] == 25
    assert len(result['results']) == 10
    assert len(agent_resources.search_resources('', limit=3)['results']) == 3


@pytest.mark.parametrize('query', [None, 1, 'x'*241, 'browser\x00use', 'browser\nuse'])
def test_invalid_queries_are_rejected(query):
    with pytest.raises(ValueError, match='query'):
        agent_resources.search_resources(query)


@pytest.mark.parametrize('limit', [0, 11, True, 1.5, '3'])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError, match='1 and 10'):
        agent_resources.search_resources(limit=limit)


def test_rendering_keeps_capability_and_setup_distinct():
    rendered = agent_resources.render_resources(agent_resources.search_resources('browser-use', limit=1))
    assert 'Owner-reviewed adapter' in rendered
    assert 'check runtime in Integrations' in rendered
    assert 'Navigate interactive websites' in rendered
    assert 'Requirements:' in rendered and 'License:' in rendered and 'Source: https://' in rendered
    assert 'public HTTPS reader' in rendered
    assert 'no account flows or qualified model-driven actions' in rendered


def test_invalid_availability_fails_closed(tmp_path, monkeypatch):
    data = json.loads(agent_resources.CATALOG_PATH.read_text(encoding='utf-8'))
    data['resources'][0]['availability'] = 'installed_and_tested'
    catalog = tmp_path / 'resources.json'
    catalog.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(agent_resources, 'CATALOG_PATH', catalog)
    with pytest.raises(ValueError, match='invalid record'):
        agent_resources.search_resources()


def test_all_browses_bounded_results_and_each_supplement_is_searchable():
    all_rows = agent_resources.search_resources(' ALL ')
    assert all_rows['total_matches'] == 15
    assert len(all_rows['results']) == 10
    assert all_rows['results'] == agent_resources.search_resources('')['results']
    assert 'Showing 10 of 15' in agent_resources.render_resources(all_rows)
    expected = {
        'Awesome-Free-LLM-APIs': 'awesome-free-llm-apis',
        'Heretic': 'heretic', 'Guards-I-View': 'gods-eye-view',
        'Scientific agent skills': 'scientific-agent-skills',
        'Diagram designs': 'diagram-design',
        'Anthropic cybersecurity skills': 'anthropic-cybersecurity-skills',
        'Awesome harness engineering': 'awesome-harness-engineering',
        'breach directory': 'breach-directory',
        'GitHub-Leaked-API-Keys-and-Secrets.md': 'leaked-credentials-request',
        'HIBP': 'have-i-been-pwned',
    }
    for query, identity in expected.items():
        row = agent_resources.search_resources(query)['results'][0]
        assert row['id'] == identity
        assert row['availability'] != 'existing-public-adapter'


def test_unverified_identity_and_credential_request_do_not_imply_working_integration():
    leaked = agent_resources.search_resources('GitHub-Leaked-API-Keys-and-Secrets.md')['results'][0]
    assert leaked['source_url'] is None
    assert leaked['availability'] == 'ambiguous/not-integrated'
    assert 'not provider-issued' in leaked['limitations']
    assert 'Source: Not verified' in agent_resources.render_resources({'results': [leaked]})
    breach = agent_resources.search_resources('breach directory')['results'][0]
    assert breach['verification_status'] == 'unverified'
    assert breach['availability'] == 'ambiguous/not-integrated'
    community = agent_resources.search_resources('Anthropic cybersecurity skills')['results'][0]
    assert 'mukul975' in community['publisher']
    assert 'not affiliated with Anthropic' in community['limitations']


def test_missing_source_is_only_allowed_for_unintegrated_records(tmp_path, monkeypatch):
    data = json.loads(agent_resources.CATALOG_PATH.read_text(encoding='utf-8'))
    data['resources'][0]['source_url'] = None
    catalog = tmp_path / 'resources.json'
    catalog.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(agent_resources, 'CATALOG_PATH', catalog)
    with pytest.raises(ValueError, match='invalid record'):
        agent_resources.search_resources()
