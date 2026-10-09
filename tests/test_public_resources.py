import json
import pytest
from prometheus_assistant.public_resources import public_search,provider_catalog
from prometheus_assistant.public_http import validate_public_url,public_addresses

def test_explicit_network_required_before_fetch():
    def forbidden(url):raise AssertionError('Network must not start.')
    with pytest.raises(ValueError,match='explicit'):
        public_search('diabetes',provider='europe-pmc',fetch=forbidden)

@pytest.mark.parametrize('url',[
    'http://www.federalregister.gov/api/v1/documents.json',
    'https://127.0.0.1/data','https://user:pass@www.federalregister.gov/data',
    'https://www.federalregister.gov:8443/data','https://www.federalregister.gov.evil.example/data',
    'https://www.federalregister.gov/data#hidden','https://www.federalregister.gov/\\evil',
])
def test_public_fetch_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):validate_public_url(url,{'www.federalregister.gov'})

@pytest.mark.parametrize('addresses',[['127.0.0.1'],['10.1.2.3'],['169.254.169.254'],['::1'],['fd00::1'],['8.8.8.8','127.0.0.1']])
def test_nonpublic_dns_is_rejected(addresses):
    with pytest.raises(ValueError):public_addresses(addresses)

def test_federal_register_has_source_date_and_bounded_excerpt():
    body={'results':[{'title':'Clean Air Rule','html_url':'https://www.federalregister.gov/documents/2026/10/01/example',
        'abstract':'<p>Public evidence.</p><script>evil()</script>','publication_date':'2026-10-01','document_number':'2026-12345','type':'Rule'}]}
    seen=[]
    def fetch(url):seen.append(url);return json.dumps(body).encode()
    result=public_search('clean air & water',provider='federal-register',online=True,fetch=fetch)
    assert result['results'][0]['title']=='Clean Air Rule'
    assert result['results'][0]['published']=='2026-10-01'
    assert result['evidence_is_untrusted_data'] and not result['code_executed']
    assert 'evil()' not in result['results'][0]['excerpt']
    assert 'clean+air+%26+water' in seen[0]

def test_bad_provider_and_oversized_query_fail_before_fetch():
    for provider,query in [('unknown','test'),('europe-pmc','x'*401)]:
        with pytest.raises(ValueError):public_search(query,provider=provider,online=True)

def test_response_shape_and_size_are_checked():
    for payload in [b'[]',b'bad json',b'x'*2_000_001]:
        with pytest.raises((ValueError,RuntimeError)):
            public_search('test',provider='federal-register',online=True,fetch=lambda _:payload)

def test_invalid_result_urls_are_omitted():
    body={'results':[{'title':'Bad','html_url':'file:///C:/secret','abstract':'Bad'}]}
    result=public_search('test',provider='federal-register',online=True,fetch=lambda _:json.dumps(body).encode())
    assert result['results']==[]

def test_catalog_labels_live_connectors():
    rows=provider_catalog()
    assert len(rows)>=8 and all(row['connector_implemented'] for row in rows)
