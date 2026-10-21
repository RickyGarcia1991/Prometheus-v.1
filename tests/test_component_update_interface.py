"""Update features through the same job interface used by the installed UI."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from prometheus_assistant import ui_server, ui_workspace

@pytest.fixture
def app(tmp_path):
    value=ui_server.Interface(tmp_path/'memory.sqlite3')
    # These tests exercise update scheduling/ownership, not the host's transient
    # battery or memory pressure. Keep real resource admission with fixed inputs.
    value.project_resources.probe = SimpleNamespace(snapshot=lambda **kwargs: dict(
        total_ram_gib=16, available_ram_gib=8, free_disk_gib=20, cpu_percent=10,
        battery_percent=80, plugged_in=True, low_memory=False, thermal_distress=False,
        observed_at='fixture'))
    yield value
    value.close()

def finished(app, body):
    job=app.submit(body)
    for _ in range(500):
        with app.lock:
            row=app.jobs[job['id']].copy()
        if row['state']!='running':return row
        time.sleep(.01)
    pytest.fail('Bounded update job did not finish')

def test_shipped_catalog_and_persistent_update_controls(app,monkeypatch):
    from prometheus_assistant import public_http
    monkeypatch.setattr(public_http,'fetch_public_bytes',lambda *a,**k:pytest.fail('Status/settings used network'))
    first=finished(app,{'mode':'component_updates'})
    assert first['state']=='done', first
    rows=first['result']['details']['entries']
    assert len(rows)>=100
    assert {'model','package','runtime','resource','archive','application'}<={r['category'] for r in rows}
    assert first['result']['details']['enabled'] is True
    paused=finished(app,{'mode':'configure_updates','enabled':False})
    assert paused['result']['details']['enabled'] is False
    assert ui_workspace.component_updates(app).status()['due_count']==0
    resumed=finished(app,{'mode':'configure_updates','enabled':True})
    assert resumed['result']['details']['enabled'] is True
    assert not app.memory_path.exists(), 'Maintenance must not add private conversation turns'

def test_configuration_requires_boolean_and_explicit_choice(app):
    with pytest.raises(ValueError):app.submit({'mode':'configure_updates','enabled':'yes'})
    assert finished(app,{'mode':'configure_updates'})['state']=='error'

def test_idle_alternates_due_sources_and_updates_without_interrupting_work(app,monkeypatch):
    class Watch:
        def status(self):return {'watches':[{'enabled':True,'due':True}]}
    class Updates:
        def status(self):return {'due_count':1}
    monkeypatch.setattr(ui_workspace,'schedule',lambda _:Watch())
    monkeypatch.setattr(ui_workspace,'component_updates',lambda _:Updates())
    submitted=[]
    monkeypatch.setattr(app,'submit',lambda body, **options: submitted.append({**body, **options}))
    app.current='foreground'
    app.next_maintenance=0;app.idle_maintenance();assert not submitted
    app.current=None
    app.next_maintenance=0;app.idle_maintenance()
    app.next_maintenance=0;app.idle_maintenance()
    assert [row['mode'] for row in submitted]==['check_sources','check_updates']
    assert all(row['background'] is True for row in submitted)
    app.closing=True
    app.next_maintenance=0;app.idle_maintenance();assert len(submitted)==2

def test_manual_check_is_bounded_and_cancellable(app,monkeypatch):
    entered=threading.Event();seen=[]
    class Updates:
        def run_due_checks(self,**kwargs):
            seen.append(kwargs);entered.set()
            assert kwargs['cancel_event'].wait(3)
            return {'enabled':True,'entries':[],'checks':[{'status':'cancelled'}]}
    monkeypatch.setattr(ui_workspace,'component_updates',lambda _:Updates())
    job=app.submit({'mode':'check_updates','enabled':True})
    assert entered.wait(2)
    assert app.jobs[job['id']]['cancellable']
    app.cancel(job['id'])
    for _ in range(200):
        if app.current is None:break
        time.sleep(.01)
    assert app.current is None
    assert seen[0]['max_checks']==1 and seen[0]['force'] is True
    assert seen[0]['library'].embedding_client is None

def test_failed_update_catalog_does_not_block_existing_source_watch(app,monkeypatch):
    class Watch:
        def status(self):return {'watches':[{'enabled':True,'due':True}]}
    monkeypatch.setattr(ui_workspace,'schedule',lambda _:Watch())
    def broken(_):raise ValueError('Catalog damaged')
    monkeypatch.setattr(ui_workspace,'component_updates',broken)
    submitted=[]
    monkeypatch.setattr(app,'submit',lambda body, **options: submitted.append({**body, **options}))
    app.next_maintenance=0;app.idle_maintenance()
    assert submitted==[{'mode':'check_sources','prompt':'','background':True}]
    assert 'Catalog damaged' in app.maintenance_error


@pytest.mark.parametrize('mode', ['check_sources', 'check_updates'])
@pytest.mark.parametrize('outcome', ['done', 'error', 'cancelled'])
def test_scheduled_job_origin_survives_running_and_terminal_states(app, monkeypatch, mode, outcome):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(ui_server, 'hardware_status', lambda: {'fixture': True})
    def work(request):
        entered.set()
        assert release.wait(3)
        if outcome == 'error':
            raise ValueError('Synthetic scheduled failure')
        if outcome == 'cancelled':
            raise ui_server.GenerationCancelled('Synthetic scheduled cancellation')
        return {'reply': 'Background report', 'details': {'checks': []}}
    monkeypatch.setattr(app, 'execute', work)
    job = app.submit({'mode': mode}, background=True)
    try:
        assert entered.wait(2)
        with app.lock:
            assert app.jobs[job['id']]['background'] is True
        status = app.status()
        assert status['job'] == job['id'] and status['job_background'] is True and status['busy'] is True
    finally:
        release.set()
    for _ in range(300):
        with app.lock:
            result = app.jobs[job['id']].copy()
        if result['state'] != 'running':
            break
        time.sleep(.01)
    assert result['state'] == outcome
    assert result['background'] is True
    assert app.status()['job_background'] is False and app.state == 'idle'
    assert not app.memory_path.exists(), 'scheduled status is not a conversation exchange'


@pytest.mark.parametrize('mode', ['check_sources', 'check_updates'])
def test_explicit_maintenance_is_not_background(app, monkeypatch, mode):
    monkeypatch.setattr(app, 'execute', lambda request: {'reply': 'Requested report'})
    result = finished(app, {'mode': mode})
    assert result['background'] is False and result['result']['reply'] == 'Requested report'


def test_background_marker_cannot_be_supplied_by_http_request_or_hide_conversation(app):
    with pytest.raises(ValueError):
        app.submit({'mode': 'check_updates', 'background': True})
    with pytest.raises(ValueError):
        app.submit({'mode': 'chat', 'prompt': 'Owner request'}, background=True)
    assert app.current is None
