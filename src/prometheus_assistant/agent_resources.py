"""Offline curated resource discovery; reference records never install tools."""
import json
from pathlib import Path
import re


CATALOG_PATH = Path(__file__).with_name('agent_resources.json')
MAX_RESULTS = 10
AVAILABILITIES = {'reference', 'existing-public-adapter', 'reviewed-adapter', 'requires-setup', 'ambiguous/not-integrated'}
REQUIRED_TEXT = ('id', 'name', 'publisher', 'category', 'license',
                 'availability', 'capability', 'limitations', 'next_step', 'verified_on')
NOTE = ('Curated reference information checked on the listed dates. Setup requirements are separate from '
        'working Prometheus capabilities. Searching this catalogue performs no network requests or installation.')


def _normalized(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def _catalog():
    with CATALOG_PATH.open('rb') as stream:
        body = stream.read(256_001)
    if len(body) > 256_000:
        raise ValueError('The agent resource catalogue exceeds its size limit.')
    data = json.loads(body)
    if (not isinstance(data, dict) or data.get('schema') != 1
            or not isinstance(data.get('resources'), list) or len(data['resources']) > 200):
        raise ValueError('The agent resource catalogue has an unsupported format.')
    ids = set()
    for row in data['resources']:
        if (not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                for key in REQUIRED_TEXT) or row['availability'] not in AVAILABILITIES
                or row['id'] in ids or 'source_url' not in row
                or not ((isinstance(row.get('source_url'), str) and row['source_url'].startswith('https://'))
                        or (row.get('source_url') is None and row['availability'] == 'ambiguous/not-integrated'))
                or not isinstance(row.get('requirements'), list)
                or any(not isinstance(value, str) for value in row['requirements'])
                or not isinstance(row.get('aliases', []), list)
                or any(not isinstance(value, str) for value in row.get('aliases', []))
                or (row.get('source_commit') is not None and
                    (not isinstance(row['source_commit'], str)
                     or not re.fullmatch(r'[0-9a-f]{40}', row['source_commit'])))):
            raise ValueError('The agent resource catalogue contains an invalid record.')
        ids.add(row['id'])
    return data


def search_resources(query='', *, limit=MAX_RESULTS):
    """Return exact matches first, then case-insensitive all-term partial matches.

    Empty queries and 'all' browse the bounded catalogue. Results are freshly decoded on
    every call so callers cannot mutate the next lookup's reference records.
    """
    if not isinstance(query, str) or len(query) > 240 or any(ord(char) < 32 for char in query):
        raise ValueError('Use an agent resource query of at most 240 characters without control characters.')
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError('Choose between 1 and 10 agent resource results.')
    data = _catalog()
    wanted = '' if query.strip().casefold() == 'all' else _normalized(query)
    terms = wanted.split()
    matches = []
    for index, row in enumerate(data['resources']):
        identities = [_normalized(row[key] or '') for key in ('id', 'name', 'source_url')]
        identities += [_normalized(value) for value in row.get('aliases', [])]
        exact = bool(wanted and wanted in identities)
        searchable = _normalized(' '.join([*identities, row['publisher'], row['category'],
            row['capability'], row['limitations'], *row['requirements']]))
        if exact or not terms or all(term in searchable for term in terms):
            matches.append((0 if exact else 1, index, row))
    matches.sort(key=lambda match: match[:2])
    return {'query': query.strip(), 'results': [row for _, _, row in matches[:limit]],
            'total_matches': len(matches), 'verified_on': data.get('verified_on'),
            'network_used': False, 'note': NOTE}


def render_resources(result):
    """Concise conversation text while the full structured result stays available."""
    rows = result['results'][:MAX_RESULTS]
    if not rows:
        return ('No catalogue entry matches that query. Supply an exact project or publisher name. '
                + NOTE)
    labels = {'reference': 'External reference', 'existing-public-adapter': 'Existing public adaptation',
              'reviewed-adapter': 'Owner-reviewed adapter · check runtime in Integrations',
              'requires-setup': 'Requires setup; adapter not installed',
              'ambiguous/not-integrated': 'Not integrated; see verification limits'}
    parts = []
    if result.get('total_matches', len(rows)) > len(rows):
        parts.append(f"Showing {len(rows)} of {result['total_matches']} matches. Search a project or capability to narrow the list.")
    for row in rows:
        parts.append(f"{row['name']} — {labels[row['availability']]}\n"
                     f"{row['capability']}\n"
                     f"Publisher: {row['publisher']}. License: {row['license']}.\n"
                     f"Requirements: {'; '.join(row['requirements'])}.\n"
                     f"Limits: {row['limitations']}\n"
                     f"Next step: {row['next_step']}\n"
                     f"Source: {row['source_url'] or 'Not verified; no resource link supplied'}")
    return '\n\n'.join(parts) + '\n\n' + NOTE
