"""Explicit, bounded Firecrawl web search. No SDK, account or local service required."""
from datetime import datetime, timezone
import hashlib
import json
import os
from urllib.parse import urlsplit, urlunsplit

from .public_http import post_public_json, PublicHTTPError
from .public_resources import text_only, safe_reference_url

ENDPOINT = 'https://api.firecrawl.dev/v2/search'


def search(query, *, cancel_event=None):
    from .application_policy import require_dispatch
    from .public_reach import validate_request, public_url, _check_cancel
    require_dispatch('reach.read')
    require_dispatch('public.fetch')
    validate_request('web-search', query)
    _check_cancel(cancel_event)
    # A host may explicitly supply a key for a higher quota. Never load browser
    # accounts or portable configuration secrets; keyless is the default.
    key = os.environ.get('PROMETHEUS_FIRECRAWL_API_KEY') or None
    try:
        body = post_public_json(ENDPOINT, {'api.firecrawl.dev'},
            {'query': query, 'limit': 5, 'sources': ['web'], 'domainTools': False,
             'highlights': False, 'timeout': 20000},
            bearer_token=key, max_bytes=1_000_000, timeout=25, overall_timeout=35,
            cancel_event=cancel_event)
    except PublicHTTPError as error:
        _check_cancel(cancel_event)
        messages = {
            401: 'Firecrawl rejected the API key. Check the host-local PROMETHEUS_FIRECRAWL_API_KEY setting, or remove it to use limited keyless access.',
            402: 'Firecrawl search quota is exhausted. Wait for the allowance to renew or configure your own Firecrawl account on this computer.',
            403: 'Firecrawl denied search access. Check service availability and your account settings if a key is configured.',
            408: 'Firecrawl search timed out. Try again later.',
            429: 'Firecrawl search rate limit reached. Wait before trying again; keyless access has a limited allowance.',
        }
        raise RuntimeError(messages.get(error.status, f'Firecrawl search is unavailable (HTTP {error.status}). Try again later.')) from None
    except Exception:
        _check_cancel(cancel_event)
        raise
    _check_cancel(cancel_event)
    if not isinstance(body, bytes) or len(body) > 1_000_000:
        raise RuntimeError('Firecrawl search response exceeded its size limit.')
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Firecrawl returned invalid search data.') from error
    if (not isinstance(data, dict) or data.get('success') is not True
            or not isinstance(data.get('data'), dict) or not isinstance(data['data'].get('web'), list)):
        raise ValueError('Firecrawl did not return a successful web search.')
    rows, seen = [], set()
    for item in data['data']['web'][:5]:
        if not isinstance(item, dict) or not isinstance(item.get('url'), str):
            raise ValueError('Firecrawl returned invalid search results.')
        url = item['url']
        try:
            if not safe_reference_url(url) or len(url) > 2000:
                raise ValueError('Unsafe citation URL.')
            parsed = urlsplit(url)
            public_url(urlunsplit(parsed._replace(fragment='')))
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        title, description = item.get('title', ''), item.get('description', '')
        if not isinstance(title, str) or not isinstance(description, str):
            raise ValueError('Firecrawl returned invalid result text.')
        rows.append({'title': text_only(title, 240) or url, 'url': url,
                     'excerpt': text_only(description, 2000), 'excerpt_truncated': len(description) > 2000})
    note = ('Search query sent to Firecrawl. Results are source links and snippets; '
            'linked pages were not fetched. ' +
            ('A host-configured API key was used; service usage may count against its plan.' if key else
             'Limited keyless access was used; availability and rate limits depend on Firecrawl.'))
    reply = '\n\n'.join(f"{i}. {row['title']}\n{row['excerpt']}\n{row['url']}" for i, row in enumerate(rows, 1))
    return {'action': 'web-search', 'provider': 'firecrawl', 'target': query, 'endpoint': ENDPOINT,
            'results': rows, 'reply': (reply or 'No public web results were returned for this query.') + '\n\n' + note,
            'note': note, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
            'response_sha256': hashlib.sha256(body).hexdigest(), 'response_bytes': len(body),
            'evidence_is_untrusted_data': True, 'generated_answer': False,
            'code_executed': False, 'offline': False}
