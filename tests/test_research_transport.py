import pytest
from prometheus_assistant.research_http import HttpJsonResearchProvider, NoResearchRedirects


@pytest.mark.parametrize('endpoint', [
    'https://user:pass@search.example/api',
    'https:///missing-host',
    'https://search.example/api#fragment',
])
def test_provider_rejects_unsafe_endpoint(endpoint):
    with pytest.raises(ValueError):
        HttpJsonResearchProvider(endpoint)


def test_redirects_are_rejected_without_following():
    handler = NoResearchRedirects()
    with pytest.raises(RuntimeError, match='redirects are disabled'):
        handler.redirect_request(None, None, 302, 'redirect', {}, 'http://unsafe.example')
