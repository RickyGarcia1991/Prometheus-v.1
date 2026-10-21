from copy import deepcopy
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import specialist_resources as resources
from prometheus_assistant.project_resources import ProjectResources


def observation(**changes):
    return {**dict(total_ram_gib=16, available_ram_gib=8, free_disk_gib=20, cpu_percent=10,
        battery_percent=80, plugged_in=True, low_memory=None, thermal_distress=None,
        observed_at='fixture'), **changes}


def app_for(tmp_path, values):
    calls = []
    def snapshot(**kwargs):
        calls.append(kwargs)
        return deepcopy(values[min(len(calls)-1, len(values)-1)])
    app = SimpleNamespace(lock=threading.Lock(),
        project_resources=ProjectResources(tmp_path, probe=SimpleNamespace(snapshot=snapshot)))
    return app, calls


def test_every_stage_uses_fresh_probe_without_history_disk_or_cost_estimate(tmp_path):
    app, calls = app_for(tmp_path, [observation(), observation(available_ram_gib=1)])
    app.project_resources.disk_probe = SimpleNamespace(snapshot=lambda: pytest.fail('disk sampled'))
    first, second = resources.admit(app), resources.admit(app)
    assert calls == [{'force': True}, {'force': True}]
    assert first['decision'] == 'run_local' and first['reasons'] == []
    assert first['model_budget'] == 'runtime_profile_and_context'
    assert second['decision'] == 'defer' and 'ram_reserve' in second['reason_codes']
    assert not (tmp_path/'Maintenance').exists()


@pytest.mark.parametrize('change,reason', [
    ({'available_ram_gib': None}, 'ram_unknown'),
    ({'available_ram_gib': float('nan')}, 'ram_unknown'),
    ({'available_ram_gib': True}, 'ram_unknown'),
    ({'available_ram_gib': 20}, 'ram_unknown'),
    ({'available_ram_gib': 2}, 'ram_reserve'),
    ({'cpu_percent': None}, 'cpu_unknown'),
    ({'cpu_percent': 85}, 'cpu_busy'),
    ({'plugged_in': None}, 'power_unknown'),
    ({'plugged_in': False}, 'battery_budget_unknown'),
    ({'low_memory': True}, 'low_memory'),
    ({'thermal_distress': True}, 'thermal_distress'),
])
def test_unknown_or_insufficient_measurements_defer(tmp_path, change, reason):
    app, _ = app_for(tmp_path, [observation(**change)])
    result = resources.admit(app)
    assert result['decision'] == 'defer' and reason in result['reason_codes']


def test_measurement_errors_are_redacted_and_saved_validation_never_samples(tmp_path):
    app, _ = app_for(tmp_path, [observation()])
    def broken(**_):
        raise OSError('private host path')
    app.project_resources.probe.snapshot = broken
    result = resources.admit(app)
    assert result['reason_codes'] == ['ram_unknown', 'cpu_unknown', 'power_unknown', 'mode_unknown']
    assert 'private' not in str(result)
    assert resources.validate(result) == result
    result['decision'] = 'run_local'
    with pytest.raises(ValueError):
        resources.validate(result)


def test_busy_slot_and_bounded_saved_resources(tmp_path):
    app, _ = app_for(tmp_path, [observation()])
    result = resources.admit(app, slot_available=False)
    assert result['reason_codes'] == ['model_slot_busy']
    for mutate in (lambda x: x.update(authorized=True),
                   lambda x: x.update(observed_at='x'*81),
                   lambda x: x['observations'].update(cpu_percent=float('inf')),
                   lambda x: x.update(max_model_concurrency=True)):
        changed = deepcopy(result)
        mutate(changed)
        with pytest.raises(ValueError):
            resources.validate(changed)


def test_explicit_idle_prevents_optional_inference_admission(tmp_path):
    app,_=app_for(tmp_path,[observation(free_disk_gib=20)])
    app.project_resources.set_mode_preference('Idle')
    result=resources.admit(app)
    assert result['schema_version']==2
    assert result['decision']=='defer' and 'mode_idle' in result['reason_codes']


@pytest.mark.parametrize('mode,code',[('Idle','mode_idle'),('Battery Saver','mode_battery'),('Critical Recovery','mode_critical')])
def test_restricted_modes_and_legacy_receipt_validation(tmp_path,mode,code):
    app,_=app_for(tmp_path,[observation()])
    app.project_resources.set_mode_preference(mode)
    result=resources.admit(app)
    assert code in result['reason_codes'] and result['decision']=='defer'
    assert resources.validate(result)==result
    legacy=deepcopy(result);legacy.pop('mode');legacy['schema_version']=1
    legacy['reason_codes']=[];legacy['reasons']=[];legacy['decision']='run_local'
    assert resources.validate(legacy)==legacy
    tampered=deepcopy(result);tampered['reason_codes']=[];tampered['reasons']=[];tampered['decision']='run_local'
    with pytest.raises(ValueError):resources.validate(tampered)


def test_automatic_idle_dwell_does_not_block_foreground_specialist(tmp_path):
    app,_=app_for(tmp_path,[observation()])
    assert app.project_resources.status()['mode']=='Idle'
    result=resources.admit(app)
    assert result['mode']==dict(requested=None,effective='Idle',dwell_pending=True)
    assert result['decision']=='run_local'


@pytest.mark.parametrize('field,value',[('effective','Unknown'),('requested',True),('dwell_pending',1)])
def test_new_mode_receipt_rejects_invalid_fields(tmp_path,field,value):
    app,_=app_for(tmp_path,[observation()]);result=resources.admit(app)
    result['mode'][field]=value
    with pytest.raises(ValueError):resources.validate(result)


def test_forged_automatic_idle_exception_does_not_hide_explicit_idle(tmp_path):
    app,_=app_for(tmp_path,[observation()]);app.project_resources.set_mode_preference('Idle')
    result=resources.admit(app)
    result['mode']['dwell_pending']=True
    result['decision']='run_local';result['reason_codes']=[];result['reasons']=[]
    with pytest.raises(ValueError):resources.validate(result)
    result['mode']=dict(requested=None,effective='Idle',dwell_pending=False)
    with pytest.raises(ValueError):resources.validate(result)
