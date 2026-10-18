"""Real owner-bound HTTP reads cannot grant, arm, expire or dispatch work."""
import hashlib
import time
import copy
from types import SimpleNamespace

from test_grants_http import endpoint
from prometheus_assistant import scheduled_syntax_ui

ROUTE = '/api/decision-center'


def prepare(endpoint, monkeypatch):
    app, gate, effects, request = endpoint
    monkeypatch.setattr(app.project_resources, 'status', lambda **kw: {'mode': 'Idle', 'observations': {}})
    app._decision_resource_status = {'mode': 'Idle', 'observations': {}}
    app._decision_resource_cached_at = time.monotonic()
    with gate.grant_owner('fixture-cookie') as owner:
        pending = app.action_approvals.review(owner=owner,
            binding=hashlib.sha256(b'fixture-cookie').hexdigest(), session='a'*32,
            kind='project_task', payload={'secret': 'private source'},
            review={'secret': 'private prompt'}, policy_revision='fixture')
    calls = []
    def queue(instance, current_owner):
        assert instance is app and current_owner == owner
        calls.append(current_owner)
        return SimpleNamespace(list=lambda: [])
    monkeypatch.setattr(scheduled_syntax_ui, 'queue', queue)
    return app, gate, effects, request, pending, calls


def test_attention_http_owner_token_and_origin(endpoint, monkeypatch):
    app, gate, effects, request, pending, calls = prepare(endpoint, monkeypatch)
    for kwargs, expected in [({'owner': False}, 401), ({'token': False}, 403),
                             ({'origin': 'https://foreign.example'}, 403)]:
        assert request(ROUTE, **kwargs)[0] == expected
    assert not calls
    code, data = request(ROUTE)
    assert code == 200 and data['read_only'] is True
    assert data['items'] and 'private' not in str(data)
    assert len(calls) == 1 and not effects
    assert not hasattr(app, '_scheduled_syntax_owner')
    with gate.grant_owner('fixture-cookie') as owner:
        assert app.action_approvals.pending(owner=owner,
            binding=hashlib.sha256(b'fixture-cookie').hexdigest()) == pending
    gate.sessions.clear()
    assert request(ROUTE)[0] == 401
    assert len(calls) == 1


def test_unavailable_queue_does_not_hide_pending_review(endpoint, monkeypatch):
    app, gate, effects, request, pending, calls = prepare(endpoint, monkeypatch)
    def unavailable(*args):
        raise ValueError('private path must not leak')
    monkeypatch.setattr(scheduled_syntax_ui, 'queue', unavailable)
    code, data = request(ROUTE)
    assert code == 200
    assert data['sources']['queue']['state'] == 'unavailable'
    assert data['items'] and 'private' not in str(data)
    assert not effects and not hasattr(app, '_scheduled_syntax_owner')


def test_resource_failure_is_explicit_and_does_not_dispatch(endpoint, monkeypatch):
    app, gate, effects, request, pending, calls = prepare(endpoint, monkeypatch)
    def unavailable(**kw):
        raise OSError('private counter details')
    monkeypatch.setattr(app.project_resources, 'status', unavailable)
    app._decision_resource_cached_at -= 60
    code, data = request(ROUTE)
    assert code == 200 and data['sources']['resources']['state'] == 'unavailable'
    assert 'private' not in str(data) and not effects


def test_read_never_expires_proposals_or_samples_resources(endpoint, monkeypatch):
    app, gate, effects, request, pending, calls = prepare(endpoint, monkeypatch)
    app.action_approvals.clock = lambda: pending['expires_at'] + 1
    before = copy.deepcopy(app.action_approvals._pending)
    def forbidden(**kw):
        raise AssertionError('The dashboard must not sample resources')
    monkeypatch.setattr(app.project_resources, 'status', forbidden)
    code, data = request(ROUTE)
    assert code == 200 and app.action_approvals._pending == before
    assert not any(row['source'] == 'approvals' for row in data['items'])
    assert data['sources']['resources']['state'] == 'available'
    assert not effects


def test_activity_failure_keeps_review_visible(endpoint, monkeypatch):
    app, gate, effects, request, pending, calls = prepare(endpoint, monkeypatch)
    def failed():
        raise ValueError('private history')
    monkeypatch.setattr(app.activity_history, 'recent', failed)
    code, data = request(ROUTE)
    assert code == 200 and data['sources']['activity']['state'] == 'unavailable'
    assert any(row['source'] == 'approvals' for row in data['items'])
    assert 'private' not in str(data)
