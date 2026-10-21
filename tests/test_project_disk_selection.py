"""Selection changes must never relabel readings from the previous project."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import disk_pressure_observations as disk, ui_workspace
from prometheus_assistant.project_resources import ProjectResources
from test_disk_pressure_observations import packet, probe, encode


def shared_packet(step=0):
    value = packet(step)
    volume = dict(value['volumes'][0], role='project')
    value['volumes'].append(volume)
    return value


def test_selection_clears_old_numbers_without_extra_helper_or_cross_project_delta():
    instance, now, runner = probe([packet(), packet(1), shared_packet(2), shared_packet(3)])
    instance.snapshot()
    now[0] = 10
    assert instance.snapshot()['status'] == 'observed'
    instance.select_project('E:/project-a')
    pending = instance.snapshot()
    assert pending['reason_code'] == 'selection_changed'
    assert pending['devices'] == [] and pending['observed_at'] is None
    now[0] = 15
    instance.select_project('E:/project-b')
    assert instance.snapshot()['devices'] == []
    assert len(runner.calls) == 2
    now[0] = 20
    warm = instance.snapshot()
    assert warm['status'] == 'warming_up'
    assert warm['devices'][0]['latency_ms'] is None
    assert runner.calls[-1] == {'data': 'E:\\data', 'project': 'E:\\project-b'}
    now[0] = 30
    measured = instance.snapshot()
    assert measured['status'] == 'observed'
    assert len(measured['devices']) == 1
    assert set(measured['devices'][0]['roles']) == {'data', 'project'}
    assert measured['devices'][0]['latency_ms'] == pytest.approx(4)
    assert 'project-b' not in json.dumps(measured)


def test_same_windows_selection_keeps_cache_and_interval():
    instance, now, runner = probe([shared_packet(), shared_packet(1)])
    instance.select_project('E:/Project')
    instance.snapshot()
    now[0] = 5
    instance.select_project('e:\\project\\')
    assert instance.snapshot()['status'] == 'warming_up'
    assert len(runner.calls) == 1
    now[0] = 10
    assert instance.snapshot()['status'] == 'observed'


def test_unsupported_project_does_not_hide_app_data_or_send_unc_to_helper():
    instance, now, runner = probe([packet(), packet(1), packet(2)])
    instance.select_project('\\\\server\\private-project')
    instance.snapshot()
    now[0] = 10
    result = instance.snapshot()
    assert result['status'] == 'partial' and not result['stale']
    assert result['devices'][0]['latency_ms'] == pytest.approx(4)
    assert result['volumes'][-1] == dict(role='project', status='unavailable', reason_code='mapping_unsupported')
    assert all(set(roots) == {'data'} for roots in runner.calls)
    assert 'private-project' not in json.dumps(result)
    instance.select_project(None)
    assert instance.snapshot()['devices'] == []
    now[0] = 20
    assert [v['role'] for v in instance.snapshot()['volumes']] == ['data']


def test_lost_project_mapping_keeps_valid_data_readings():
    values = [packet(), packet(1)]
    for value in values:
        value['volumes'].append(dict(role='project', status='unavailable', reason_code='directory_unavailable'))
        value['status'] = 'partial'
        value['reason_code'] = 'observations_incomplete'
    instance, now, runner = probe(values)
    instance.select_project('F:/removed-project')
    instance.snapshot()
    now[0] = 10
    result = instance.snapshot()
    assert result['status'] == 'partial'
    assert result['devices'][0]['status'] == 'observed'
    assert result['volumes'][1]['status'] == 'unavailable'


def test_separate_device_selection_keeps_distinct_rates_and_roles():
    def two_devices(step):
        value = packet(step)
        project = copy.deepcopy(value['devices'][0])
        project['device_id'] = 'e' * 64
        project['counters']['latency']['first'] += step * 4000000
        value['devices'].append(project)
        value['volumes'].append(dict(role='project', status='mapped', reason_code=None,
            volume_id='f'*64, device_id='e'*64, mapping_id='1'*64))
        return value
    instance, now, runner = probe([two_devices(0), two_devices(1)])
    instance.select_project('C:/project')
    instance.snapshot()
    now[0] = 10
    result = instance.snapshot()
    assert result['status'] == 'observed'
    by_role = {row['roles'][0]: row for row in result['devices']}
    assert set(by_role) == {'data', 'project'}
    assert by_role['data']['latency_ms'] == pytest.approx(4)
    assert by_role['project']['latency_ms'] == pytest.approx(8)


def test_selection_serializes_with_inflight_sample_and_viewers_share_next_read():
    entered, release = threading.Event(), threading.Event()
    now, calls = [0.0], []
    def runner(roots):
        calls.append(copy.deepcopy(roots))
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return encode(shared_packet() if 'project' in roots else packet())
    instance = disk.DiskPressureProbe({'data': 'E:/data'}, clock=lambda: now[0], runner=runner)
    with ThreadPoolExecutor(max_workers=5) as pool:
        first = pool.submit(instance.snapshot)
        assert entered.wait(3)
        selection = pool.submit(instance.select_project, 'E:/new-project')
        release.set()
        first.result(timeout=3)
        selection.result(timeout=3)
        assert instance.snapshot()['reason_code'] == 'selection_changed'
        now[0] = 10
        rows = list(pool.map(lambda _: instance.snapshot(), range(4)))
    assert len(calls) == 2
    assert all(set(row['devices'][0]['roles']) == {'data', 'project'} for row in rows)
    rows[0]['devices'].clear()
    assert instance.snapshot()['devices']


@pytest.fixture
def workspace_app(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_workspace, 'resource_root', lambda: None)
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'main.py').write_text('value = 1\n', encoding='utf-8')
    selected = []
    resources = ProjectResources(tmp_path / 'data', disk_probe=SimpleNamespace(select_project=selected.append))
    app = SimpleNamespace(memory_path=tmp_path/'data/memory.sqlite3', data_root=tmp_path/'data/Interface',
                          lock=threading.Lock(), project_resources=resources)
    return app, project, selected


def test_only_successful_open_selects_validated_root_without_sampling(workspace_app):
    app, project, selected = workspace_app
    ui_workspace.execute(app, {'mode': 'code_read', 'project': str(project), 'file': 'main.py'})
    assert selected == []
    result = ui_workspace.execute(app, {'mode': 'code_open', 'project': str(project)})
    assert result['details']['files']
    assert selected == [str(project.resolve())]
    for path in (project/'missing', app.memory_path.parent):
        with pytest.raises((ValueError, OSError)):
            ui_workspace.execute(app, {'mode': 'code_open', 'project': str(path)})
    assert selected == [str(project.resolve())]


def test_denied_open_cannot_change_selection(workspace_app, monkeypatch):
    app, project, selected = workspace_app
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', 'ui.code_open')
    with pytest.raises(ValueError, match='denied'):
        ui_workspace.execute(app, {'mode': 'code_open', 'project': str(project)})
    assert selected == []


def test_failed_history_read_does_not_select_partially_opened_project(workspace_app, monkeypatch):
    from prometheus_assistant import project_recovery
    app, project, selected = workspace_app
    def unavailable(_):
        raise OSError('history temporarily unavailable')
    monkeypatch.setattr(project_recovery, 'latest', unavailable)
    with pytest.raises(OSError):
        ui_workspace.execute(app, {'mode': 'code_open', 'project': str(project)})
    assert selected == []


def test_selection_without_windows_observer_is_noop(tmp_path):
    service = ProjectResources(tmp_path)
    service.select_project(str(tmp_path))
    assert service.disk_probe is None
