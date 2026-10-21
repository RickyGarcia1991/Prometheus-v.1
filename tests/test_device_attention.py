import copy
import json
import pytest
from prometheus_assistant.decision_center import build_snapshot


def device(**changes):
    value = dict(schema_version=1, phase='ready', generation=2, freshness='current',
        observed_at=990., expires_at=1020., ttl_seconds=30,
        counts=dict(online=1, offline=1, unknown=0, total=2), selected_state='online')
    value.update(changes)
    return value


def snapshot(value, **changes):
    args = dict(devices=value, decisions=[], jobs=[], activity=[], resources={'mode':'Idle'}, now=1000.)
    args.update(changes)
    return build_snapshot(**args)


def rows(result):
    return [row for row in result['items'] if row['source']=='devices']


def test_current_devices_are_distinct_from_saved_success_and_never_execute():
    health = device()
    before = copy.deepcopy(health)
    result = snapshot(health, jobs=[{'state':'completed','result':{'evidence':{'status':'passed'}}}])
    assert health == before
    assert result['sources']['devices']['freshness']=='current'
    assert any(row['state']=='online' and row['count']==1 for row in rows(result))
    assert any(row['state']=='offline' and row['count']==1 for row in rows(result))
    assert any(row['state']=='selected_online' for row in rows(result))
    assert all(row['navigation']=='remote' for row in rows(result))
    assert next(row for row in result['items'] if row['source']=='queue')['freshness']=='saved_records'
    assert result['read_only'] is True


@pytest.mark.parametrize('freshness,now',[('stale',1000),('current',1020),('current',989)])
def test_stale_or_future_observation_cannot_claim_online(freshness,now):
    result=snapshot(device(freshness=freshness),now=now)
    assert result['sources']['devices']['freshness']!='current'
    assert not any(row['state'] in {'online','selected_online'} for row in rows(result))
    assert any(row['severity']=='warning' for row in rows(result))


@pytest.mark.parametrize('phase',['disconnected','connecting','error'])
def test_unavailable_connection_never_inherits_online_from_history(phase):
    result=snapshot(device(phase=phase,freshness='unavailable',observed_at=None,expires_at=None,
        counts=dict(online=0,offline=0,unknown=0,total=0),selected_state='unselected'),
        decisions=[{'kind':'scheduled_syntax','decision':'approve'}])
    assert result['sources']['devices']['state']=='available'
    assert any(row['state']==phase for row in rows(result))
    assert not any(row['state']=='online' for row in rows(result))


@pytest.mark.parametrize('selection',['offline','missing','unknown','unselected'])
def test_selected_destination_state_is_explicit(selection):
    result=snapshot(device(selected_state=selection))
    assert any(row['state']=='selected_'+selection for row in rows(result))


@pytest.mark.parametrize('value',[None,{},'PRIVATE',device(generation=True),
    device(ttl_seconds=300),device(expires_at=float('nan')),device(phase='PRIVATE'),
    device(counts=dict(online=101,offline=0,unknown=0,total=101)),
    device(counts=dict(online=1,offline=0,unknown=0,total=2)),
    device(selected_state='PRIVATE'),device(extra='PRIVATE')])
def test_malformed_device_cache_is_unavailable_and_does_not_leak(value):
    result=snapshot(value)
    assert result['sources']['devices']['state']=='unavailable'
    assert any(row['severity']=='warning' for row in rows(result))
    assert 'PRIVATE' not in json.dumps(result,allow_nan=False)


def test_device_provider_error_cannot_hide_historical_failed_checks():
    result=snapshot(None,jobs=[{'state':'failed'}])
    assert any(row['source']=='queue' and row['state']=='failed' for row in result['items'])
    assert result['sources']['devices']['state']=='unavailable'
