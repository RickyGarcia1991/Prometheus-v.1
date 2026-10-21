"""Owner-configured public research with retained citations and restart scheduling.

The caller owns authentication, approval and idle detection. This module never
chooses a query from private files or conversation, follows result links, executes
source instructions, changes settings, or trains model weights. Its only writes
are a bounded research journal inside the supplied application data directory.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time

from .coding_workspace import _no_links, _write_atomic
from .public_resources import safe_reference_url, text_only

MAX_OBSERVATIONS = 200
MAX_HISTORY = 50
MAX_STATE_BYTES = 2_000_000
_RUNTIMES = {}
_RUNTIMES_LOCK = threading.Lock()
_RECALL_STOP_WORDS = frozenset('''
    about above after again against all also am an and any are as at be because
    been before being below between both but by can could did do does doing down
    during each either else even ever every few for from further had has have
    having he her here hers herself him himself his how i if in into is it its
    itself just know let like may me might more most much must my myself need
    neither no nor not of off on once only or other our ours ourselves out over
    own please same say she should show so some such tell than thank thanks that
    the their theirs them themselves then there these they thing things this
    those through to too under until up us very want was we were what when where
    which while who whom why will with would you your yours yourself yourselves
    help hello hey hi something anything everything nothing information
'''.split())


def _finite(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def _owner(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('A current authenticated owner identity is required.')


def _topic(value):
    from .public_reach import validate_request
    validate_request('web-search', value)
    return value.strip()


def _digest(row):
    content = {key: row[key] for key in ('url', 'title', 'excerpt')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class _Runtime:
    def __init__(self):
        self.lock = threading.RLock()
        self.run = threading.Lock()
        self.cancel = threading.Event()


class _Cancellation:
    def __init__(self, external, internal):
        self.external, self.internal = external, internal

    def is_set(self):
        return self.internal.is_set() or bool(self.external and self.external.is_set())

    def wait(self, timeout=None):
        """Compatible with cancellation-aware transports using Event.wait."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self.internal.wait(min(0.1, max(0, deadline - time.monotonic())) if deadline else 0.1)
        return True


def _progress(callback, message):
    if callback:
        try:
            callback(message)
        except Exception:
            # A closed activity panel must not lose already retrieved evidence.
            pass


class IdleResearch:
    def __init__(self, root, *, clock=time.time):
        raw = Path(root).expanduser()
        if not raw.is_absolute() or raw == raw.parent:
            raise ValueError('Idle research needs an absolute application data directory.')
        _no_links(raw)
        self.root = raw.resolve()
        self.path = self.root / 'idle-research.json'
        self.clock = clock
        with _RUNTIMES_LOCK:
            self._runtime = _RUNTIMES.setdefault(str(self.root).casefold(), _Runtime())

    @staticmethod
    def _empty():
        return {'version': 1, 'settings': {'topic': '', 'enabled': False, 'interval_minutes': 60,
                'owner': None, 'revision': 0, 'configured_at': None}, 'next_run_at': None,
                'consecutive_failures': 0, 'last_result': None, 'history': [], 'observations': []}

    def _now(self):
        value = self.clock()
        if not _finite(value):
            raise ValueError('Idle research requires a valid clock.')
        return value

    def _read(self):
        _no_links(self.path)
        if not self.path.exists():
            return self._empty()
        if not self.path.is_file() or self.path.stat().st_size > MAX_STATE_BYTES:
            raise ValueError('Idle research state is unavailable or exceeds its limit.')
        try:
            with self.path.open('rb') as handle:
                raw = handle.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                raise ValueError()
            state = json.loads(raw)
            settings = state['settings']
            _owner(settings['owner'])
            _topic(settings['topic'])
            if (state['version'] != 1 or type(settings['enabled']) is not bool
                    or type(settings['interval_minutes']) is not int
                    or not 15 <= settings['interval_minutes'] <= 1440
                    or type(settings['revision']) is not int or settings['revision'] < 1
                    or not _finite(settings['configured_at'])
                    or (state['next_run_at'] is not None and not _finite(state['next_run_at']))
                    or (settings['enabled'] and state['next_run_at'] is None)
                    or type(state['consecutive_failures']) is not int
                    or not 0 <= state['consecutive_failures'] <= 100
                    or not isinstance(state['history'], list) or len(state['history']) > MAX_HISTORY
                    or not isinstance(state['observations'], list)
                    or len(state['observations']) > MAX_OBSERVATIONS
                    or not all(isinstance(row, dict) for row in state['history'])
                    or (state['last_result'] is not None and not isinstance(state['last_result'], dict))):
                raise ValueError()
            for row in state['observations']:
                if (not isinstance(row, dict) or not safe_reference_url(row['url'])
                        or not isinstance(row['title'], str) or len(row['title']) > 240
                        or not isinstance(row['excerpt'], str) or len(row['excerpt']) > 2000
                        or not _finite(row['first_seen']) or not _finite(row['last_seen'])
                        or type(row['observations']) is not int or row['observations'] < 1
                        or row['content_sha256'] != _digest(row)):
                    raise ValueError()
                _topic(row['topic'])
            return state
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise ValueError('Idle research state is invalid; restore its saved journal before enabling research.') from error

    def _write(self, state):
        payload = (json.dumps(state, ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n').encode()
        if len(payload) > MAX_STATE_BYTES:
            raise ValueError('Idle research state exceeds its storage limit.')
        _no_links(self.path)
        self.root.mkdir(parents=True, exist_ok=True)
        _no_links(self.path)
        # Windows indexers and antivirus can momentarily hold the destination
        # without delete sharing. Retry only this atomic write, never network.
        for attempt in range(4):
            try:
                _write_atomic(self.path, payload)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.025 * (attempt + 1))
                _no_links(self.path)

    def configure(self, topic, enabled, interval_minutes=60, *, owner):
        """Persist an already approved setting, bound to one authenticated owner."""
        _owner(owner)
        topic = _topic(topic)
        if type(enabled) is not bool:
            raise ValueError('Choose whether idle research is enabled.')
        if type(interval_minutes) is not int or not 15 <= interval_minutes <= 1440:
            raise ValueError('Research interval must be 15 to 1,440 whole minutes.')
        with self._runtime.lock:
            state = self._read()
            previous = state['settings']
            if previous['owner'] is not None and previous['owner'] != owner:
                raise ValueError('Idle research is bound to a different owner.')
            now = self._now()
            state['settings'] = {'topic': topic, 'enabled': enabled, 'interval_minutes': interval_minutes,
                                 'owner': owner, 'revision': previous['revision'] + 1, 'configured_at': now}
            if not enabled:
                state['next_run_at'] = None
            elif not previous['enabled'] or previous['topic'] != topic:
                state['next_run_at'] = now
                state['consecutive_failures'] = 0
            elif previous['interval_minutes'] != interval_minutes:
                state['next_run_at'] = now + interval_minutes * 60
            if self._runtime.run.locked():
                state['last_result'] = {'state': 'superseded', 'finished_at': now,
                                        'reason': 'Owner changed the research configuration.'}
            self._write(state)
            self._runtime.cancel.set()
            return self.status()

    def status(self):
        with self._runtime.lock:
            state = self._read()
            running = self._runtime.run.locked()
            last = copy.deepcopy(state['last_result'])
            if last and last.get('state') == 'running' and not running:
                last['state'] = 'interrupted'
                last['reason'] = 'The previous process stopped; the saved next-run schedule is retained.'
            return {'settings': copy.deepcopy(state['settings']),
                    'due': bool(state['settings']['enabled'] and not running
                                and self._now() >= state['next_run_at']),
                    'next_run_at': state['next_run_at'], 'running': running, 'last_result': last,
                    'consecutive_failures': state['consecutive_failures'],
                    'retained_count': len(state['observations']), 'history': copy.deepcopy(state['history'])}

    @staticmethod
    def _sources(payload, topic, now):
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            raise ValueError('Public search returned invalid results.')
        rows, seen = [], set()
        for item in payload['results'][:5]:
            if (not isinstance(item, dict) or not isinstance(item.get('url'), str)
                    or not isinstance(item.get('title', ''), str)
                    or not isinstance(item.get('excerpt', ''), str)):
                raise ValueError('Public search returned an invalid source.')
            if not safe_reference_url(item['url']):
                continue
            row = {'topic': topic, 'url': item['url'], 'title': text_only(item.get('title', ''), 240),
                   'excerpt': text_only(item.get('excerpt', ''), 2000), 'first_seen': now,
                   'last_seen': now, 'observations': 1, 'evidence_is_untrusted_data': True}
            row['content_sha256'] = _digest(row)
            if row['content_sha256'] not in seen:
                seen.add(row['content_sha256'])
                rows.append(row)
        return rows

    def run_due(self, *, cancel_event=None, search=None, summarize=None, force=False, progress=None):
        """Perform at most one approved search; the caller must establish idleness.

        Search accepts (topic, cancel_event=...). Optional local summarize accepts
        ({topic, sources, evidence_is_untrusted_data}, cancel_event=...) and returns
        plain text. A summarizer failure still saves cited findings. Neither
        callback receives owner identity, paths, private files, or chat history.
        """
        if not self._runtime.run.acquire(blocking=False):
            return {'state': 'busy'}
        try:
            with self._runtime.lock:
                state = self._read()
                settings = copy.deepcopy(state['settings'])
                now = self._now()
                if not settings['enabled']:
                    return {'state': 'disabled'}
                if not force and now < state['next_run_at']:
                    return {'state': 'not_due', 'next_run_at': state['next_run_at']}
                if cancel_event is not None and cancel_event.is_set():
                    return {'state': 'cancelled'}
                self._runtime.cancel.clear()
                stop = _Cancellation(cancel_event, self._runtime.cancel)
                # Reserve the next slot before network so an interrupted process
                # cannot resume with an immediate, repeated request.
                state['next_run_at'] = now + settings['interval_minutes'] * 60
                state['last_result'] = {'state': 'running', 'topic': settings['topic'], 'started_at': now}
                self._write(state)
            result = {'state': 'complete', 'topic': settings['topic'], 'started_at': now,
                      'sources': [], 'summary': '', 'summary_state': 'not_requested',
                      'evidence_is_untrusted_data': True, 'model_weights_updated': False}
            try:
                from .public_reach import _check_cancel
                _progress(progress, 'Searching the approved public topic: ' + settings['topic'])
                _check_cancel(stop)
                if search is None:
                    from .firecrawl_search import search
                payload = search(settings['topic'], cancel_event=stop)
                _check_cancel(stop)
                result['sources'] = self._sources(payload, settings['topic'], self._now())
                _progress(progress, f"Retrieved {len(result['sources'])} public source snippets with citations.")
                if summarize is not None:
                    try:
                        evidence = {'topic': settings['topic'], 'sources': copy.deepcopy(result['sources']),
                                    'evidence_is_untrusted_data': True}
                        summary = summarize(evidence, cancel_event=stop)
                        if not isinstance(summary, str) or not summary.strip():
                            raise ValueError('The local model returned no usable summary.')
                        result.update(summary=text_only(summary, 8000), summary_state='complete')
                    except Exception as error:
                        _check_cancel(stop)
                        result.update(summary_state='failed', summary_error=text_only(str(error), 500))
                _check_cancel(stop)
            except Exception as error:
                from .ollama import GenerationCancelled
                result.update(state='cancelled' if stop.is_set() or isinstance(error, GenerationCancelled) else 'failed',
                              error=text_only(str(error), 500), sources=[])
            with self._runtime.lock:
                state = self._read()
                if state['settings']['revision'] != settings['revision']:
                    return {'state': 'superseded', 'reason': 'Owner changed the research configuration.'}
                if stop.is_set():
                    result.update(state='cancelled', error='Idle research cancelled.', sources=[], summary='')
                finished = self._now()
                result['finished_at'] = finished
                if result['state'] == 'complete':
                    new, changed, duplicates = 0, 0, 0
                    for source in result['sources']:
                        same = next((row for row in state['observations']
                                     if row['topic'] == source['topic']
                                     and row['content_sha256'] == source['content_sha256']), None)
                        if same is not None:
                            same['last_seen'] = source['last_seen']
                            same['observations'] += 1
                            duplicates += 1
                        else:
                            changed += any(row['topic'] == source['topic'] and row['url'] == source['url']
                                           for row in state['observations'])
                            state['observations'].append(source)
                            new += 1
                    state['observations'] = sorted(state['observations'], key=lambda row: row['last_seen'])[-MAX_OBSERVATIONS:]
                    state['consecutive_failures'] = 0
                    result.update(new_count=new, changed_count=changed, duplicate_count=duplicates)
                elif result['state'] == 'failed':
                    state['consecutive_failures'] = min(100, state['consecutive_failures'] + 1)
                delay = settings['interval_minutes'] * 60
                if result['state'] == 'failed':
                    delay = min(86400, delay * 2 ** min(state['consecutive_failures'] - 1, 7))
                state['next_run_at'] = finished + delay
                result['next_run_at'] = state['next_run_at']
                state['last_result'] = result
                record = {key: value for key, value in result.items() if key not in {'sources', 'summary'}}
                record['source_count'] = len(result['sources'])
                state['history'] = (state['history'] + [record])[-MAX_HISTORY:]
                self._write(state)
            _progress(progress, 'Saved cited research findings.' if result['state'] == 'complete'
                      else 'Idle research ' + result['state'] + '; next attempt follows the saved schedule.')
            return copy.deepcopy(result)
        finally:
            self._runtime.run.release()

    def recall(self, query, *, max_results=5, max_chars=8000):
        """Return matching retained evidence for grounding, never instructions."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError('Use a recall query of 1 to 4,000 characters.')
        if type(max_results) is not int or not 1 <= max_results <= 20:
            raise ValueError('Recall accepts 1 to 20 results.')
        if type(max_chars) is not int or not 256 <= max_chars <= 20_000:
            raise ValueError('Recall accepts a text limit of 256 to 20,000 characters.')
        # Whole meaningful words prevent incidental letters such as "a" or a
        # substring such as "me" in "document" from turning unrelated chat
        # into a source-grounded answer.
        terms = {word for word in re.findall(r'\w+', query.casefold())
                 if len(word) >= 3 and word not in _RECALL_STOP_WORDS}
        with self._runtime.lock:
            state = self._read()
        ranked = []
        for row in state['observations']:
            haystack = set(re.findall(r'\w+', ' '.join(
                row[key] for key in ('topic', 'title', 'excerpt')).casefold()))
            score = len(terms.intersection(haystack))
            if score:
                ranked.append((score, row['last_seen'], row))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected, rendered = [], ''
        for _, _, source in ranked:
            if len(selected) >= max_results:
                break
            row = copy.deepcopy(source)
            prefix = f"[{len(selected) + 1}] {row['title']}\n"
            suffix = f"\nSource: {row['url']}\nObserved: {row['last_seen']} (UTC Unix seconds)\n\n"
            remaining = max_chars - len(rendered) - len(prefix) - len(suffix)
            if remaining < 32:
                continue
            row['excerpt_truncated'] = len(row['excerpt']) > remaining
            row['excerpt'] = row['excerpt'][:remaining]
            row['evidence_is_untrusted_data'] = True
            selected.append(row)
            rendered += prefix + row['excerpt'] + suffix
        return {'results': selected, 'text': rendered, 'evidence_is_untrusted_data': True,
                'model_weights_updated': False}
