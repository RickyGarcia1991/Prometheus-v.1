"""Owner-bound HTTP preference contract, with isolated synthetic owner credentials."""
from contextlib import contextmanager
import pytest
from test_grants_http import endpoint

ROUTE = '/api/resources/preference'


def recorder(app, monkeypatch):
    calls = []
    def configure(mode):
        if mode not in (None, 'Idle', 'Balanced', 'Performance', 'Battery Saver', 'Critical Recovery'):
            raise ValueError('Invalid preference')
        calls.append(mode)
        return {'requested': mode, 'effective': 'Balanced', 'reason': 'fixture'}
    monkeypatch.setattr(app.project_resources, 'set_mode_preference', configure)
    return calls


def test_resource_preference_http_owner_token_origin(endpoint, monkeypatch):
    app, gate, _, request = endpoint
    calls = recorder(app, monkeypatch)
    for arguments, expected in [({'owner': False}, 401), ({'token': False}, 403),
                                ({'origin': 'https://foreign.example'}, 403)]:
        assert request(ROUTE, {'mode': 'Performance'}, **arguments)[0] == expected
    assert calls == []
    for mode in [None, 'Idle', 'Balanced', 'Performance', 'Battery Saver', 'Critical Recovery']:
        code, result = request(ROUTE, {'mode': mode})
        assert code == 200, result
        assert result['mode_preference']['requested'] == mode
    assert len(calls) == 6
    gate.sessions.clear()
    assert request(ROUTE, {'mode': 'Idle'})[0] == 401
    assert len(calls) == 6


@pytest.mark.parametrize('body', [{}, [], {'mode': None, 'approved': True}, {'mode': 'automatic'}, {'mode': 3}, {'mode': True}])
def test_resource_preference_rejects_shape_and_invalid_mode(endpoint, monkeypatch, body):
    app, _, _, request = endpoint
    calls = recorder(app, monkeypatch)
    assert request(ROUTE, body)[0] == 400
    assert calls == []


def test_resource_preference_rechecks_owner_before_mutation(endpoint, monkeypatch):
    app, gate, _, request = endpoint
    calls = recorder(app, monkeypatch)
    @contextmanager
    def revoked(session):
        raise ValueError('Owner expired before mutation')
        yield
    monkeypatch.setattr(gate, 'grant_owner', revoked)
    assert request(ROUTE, {'mode': 'Idle'})[0] == 400
    assert calls == []
