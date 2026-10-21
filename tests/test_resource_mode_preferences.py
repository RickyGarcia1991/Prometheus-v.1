from test_project_resources import service,candidate


def test_preference_persists_and_critical_defers(tmp_path):
    engine=service(tmp_path)
    selected=engine.set_mode_preference('Critical Recovery')
    assert selected['requested']==selected['effective']=='Critical Recovery'
    assert service(tmp_path).status()['mode_preference']['requested']=='Critical Recovery'
    assert engine.review(candidate(tmp_path))['decision']=='defer'


import json
import pytest
from types import SimpleNamespace
from prometheus_assistant.project_resources import ProjectResources
from prometheus_assistant.resource_scheduler import Scheduler


@pytest.mark.parametrize('mode',[None,*Scheduler.MODES])
def test_all_exact_preferences_round_trip(tmp_path,mode):
    engine=service(tmp_path);report=engine.set_mode_preference(mode)
    assert report['requested']==mode
    assert set(report)=={'requested','effective','reason','protective_override','dwell_pending','diagnostic'}
    assert service(tmp_path).status()['mode_preference']['requested']==mode
    json.dumps(report,allow_nan=False)


@pytest.mark.parametrize('bad',[True,1,{},[], 'performance','Automatic','x'*2000])
def test_invalid_input_does_not_change_file(tmp_path,bad):
    engine=service(tmp_path);engine.set_mode_preference('Balanced')
    path=tmp_path/'Maintenance/ResourceHistory/mode-preference.json';before=path.read_bytes()
    with pytest.raises(ValueError):engine.set_mode_preference(bad)
    assert path.read_bytes()==before


@pytest.mark.parametrize('raw',[b'no',b'{}',b'x'*1025,b'{"schema_version":true,"requested":"Performance"}',b'{"schema_version":1,"requested":"Idle","requested":"Performance"}'])
def test_corruption_falls_back_visibly(tmp_path,raw):
    path=tmp_path/'Maintenance/ResourceHistory/mode-preference.json';path.parent.mkdir(parents=True);path.write_bytes(raw)
    report=service(tmp_path).status()['mode_preference']
    assert report['requested'] is None and report['diagnostic']
    assert path.read_bytes()==raw


def test_protection_dwell_and_fresh_approval(tmp_path):
    now=[100.0];signals=dict(available_ram_gib=8,free_disk_gib=20,cpu_percent=10,battery_percent=80,plugged_in=True)
    engine=ProjectResources(tmp_path,probe=SimpleNamespace(snapshot=lambda **_:dict(signals)),clock=lambda:now[0])
    assert engine.set_mode_preference('Performance')['effective']=='Performance'
    assert engine.set_mode_preference('Balanced')['dwell_pending']
    assert engine.set_mode_preference('Critical Recovery')['effective']=='Critical Recovery'
    assert engine.set_mode_preference('Performance')['dwell_pending']
    now[0]+=100
    signals['available_ram_gib']=.5
    assert engine.status()['mode_preference']['protective_override']
    with pytest.raises(ValueError):
        with engine.reserve(candidate(tmp_path),'task'):pass
    assert not engine.scheduler.leases
    signals.update(available_ram_gib=8,plugged_in=False,battery_percent=10);now[0]+=100
    report=engine.status()['mode_preference']
    assert report['effective']=='Battery Saver' and report['protective_override']
    signals.update(plugged_in=True,battery_percent=80);now[0]+=100
    assert engine.status()['mode_preference']['effective']=='Performance'


def test_atomic_write_failure_keeps_old_preference(tmp_path,monkeypatch):
    from prometheus_assistant import project_resources
    engine=service(tmp_path);engine.set_mode_preference('Balanced')
    def fail(*args):raise OSError('simulated')
    monkeypatch.setattr(project_resources.os,'replace',fail)
    with pytest.raises(OSError):engine.set_mode_preference('Performance')
    assert engine.status()['mode_preference']['requested']=='Balanced'
    assert service(tmp_path).status()['mode_preference']['requested']=='Balanced'
    assert not list((tmp_path/'Maintenance/ResourceHistory').glob('.mode-*'))


def test_preference_survives_backup_restore(tmp_path):
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from prometheus_assistant.maintenance import create_backup,restore_backup
    engine=service(tmp_path);engine.set_mode_preference('Battery Saver')
    memory=tmp_path/'memory.sqlite3'
    with closing(sqlite3.connect(memory)) as db,db:db.execute('CREATE TABLE fixture(value TEXT)')
    backup=create_backup(memory,tmp_path/'backup',data_dirs={'Maintenance':tmp_path/'Maintenance'},require_separate_volume=False)
    restored=restore_backup(backup['path'],tmp_path/'restored')
    assert service(Path(restored['path'])).status()['mode_preference']['requested']=='Battery Saver'


def test_manual_critical_pause_survives_low_battery_and_sensor_failure(tmp_path):
    now=[100.0]
    engine=ProjectResources(tmp_path,clock=lambda:now[0],probe=SimpleNamespace(snapshot=lambda **_:dict(available_ram_gib=8,free_disk_gib=20,cpu_percent=10,battery_percent=10,plugged_in=False)))
    assert engine.set_mode_preference('Critical Recovery')['effective']=='Critical Recovery'
    now[0]+=100
    assert engine.status()['mode_preference']['effective']=='Critical Recovery'
    def unavailable(**kwargs):raise OSError('sensor unavailable')
    engine.probe=SimpleNamespace(snapshot=unavailable)
    report=engine.set_mode_preference('Performance')
    assert report['requested']=='Performance' and report['effective']=='Critical Recovery'
    assert report['diagnostic']=='Preference saved; current resource readings unavailable.'
    assert service(tmp_path).status()['mode_preference']['requested']=='Performance'


@pytest.mark.parametrize('refresh',['status','review'])
def test_transient_sensor_diagnostic_clears_on_success_but_corruption_remains(tmp_path,refresh):
    engine=service(tmp_path);working=engine.probe
    def unavailable(**kwargs):raise OSError('sensor unavailable')
    engine.probe=SimpleNamespace(snapshot=unavailable)
    assert engine.set_mode_preference('Performance')['diagnostic']
    engine.probe=working
    report=engine.status() if refresh=='status' else engine.review(candidate(tmp_path))
    assert report['mode_preference']['diagnostic'] is None
    path=tmp_path/'Maintenance/ResourceHistory/mode-preference.json';path.write_text('invalid')
    restored=service(tmp_path)
    report=restored.status() if refresh=='status' else restored.review(candidate(tmp_path))
    assert report['mode_preference']['diagnostic']=='Saved mode preference unavailable or invalid; Automatic selected.'


def test_specialist_snapshot_is_foreground_and_never_samples_disk_history(tmp_path,monkeypatch):
    engine=service(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('unrelated expensive observation')
    engine.disk_probe=SimpleNamespace(snapshot=forbidden)
    monkeypatch.setattr(engine,'_history',forbidden)
    snapshot=engine.specialist_snapshot()
    assert snapshot['mode']==dict(requested=None,effective='Balanced',dwell_pending=False)
