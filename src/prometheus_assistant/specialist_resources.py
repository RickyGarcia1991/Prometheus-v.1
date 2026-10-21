"""Fresh local stage admission, separate from project-check cost estimates.

This is an advisory foreground-work gate, not an OS quota or a model RAM
estimate. The existing runtime must still select a fitting model and context.
No disk observer, workload history, remote worker or retry is invoked here.
"""
from copy import deepcopy
import math

from .advisory_review import _json_bytes, _keys, _text
from .project_resources import service
from .resource_scheduler import Scheduler


_OBSERVATIONS = {'total_ram_gib', 'available_ram_gib', 'cpu_percent',
                 'battery_percent', 'plugged_in', 'low_memory', 'thermal_distress'}
_KEYS = {'schema_version', 'decision', 'reason_codes', 'reasons', 'observed_at',
         'observations', 'reserve_ram_gib', 'cpu_ceiling', 'max_model_concurrency',
         'model_budget', 'slot_available'}
_REASONS = {
    'mode_unknown': 'Current scheduling mode is unavailable; specialist review is deferred.',
    'mode_idle': 'Idle preference defers optional specialist inference.',
    'mode_battery': 'Battery Saver mode defers optional specialist inference.',
    'mode_critical': 'Critical Recovery mode defers optional specialist inference.',
    'ram_unknown': 'Available RAM is unknown; specialist review is deferred.',
    'ram_reserve': 'Available RAM does not exceed the host reserve.',
    'cpu_unknown': 'CPU headroom is unknown; specialist review is deferred.',
    'cpu_busy': 'CPU use has reached the host headroom limit.',
    'power_unknown': 'Power source is unknown; specialist review is deferred.',
    'battery_budget_unknown': 'On battery power; specialist energy use has not been measured.',
    'low_memory': 'The host reports memory pressure.',
    'thermal_distress': 'The host reports thermal distress.',
    'model_slot_busy': 'Another specialist invocation holds the local model slot.',
}


def _number(value, maximum=1_000_000):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= maximum)


def _observations(raw):
    result = {name: (raw.get(name) if _number(raw.get(name), 100 if name.endswith('percent') else 1_000_000)
                     else None) for name in _OBSERVATIONS if name not in
              {'plugged_in', 'low_memory', 'thermal_distress'}}
    for name in ('plugged_in', 'low_memory', 'thermal_distress'):
        result[name] = raw.get(name) if type(raw.get(name)) is bool else None
    total, available = result['total_ram_gib'], result['available_ram_gib']
    if total == 0:
        result['total_ram_gib'] = None
    if total is not None and available is not None and available > total:
        result['available_ram_gib'] = None
    return result


def _codes(observed, reserve, ceiling, slot_available):
    codes = []
    if observed['available_ram_gib'] is None:
        codes.append('ram_unknown')
    elif observed['available_ram_gib'] <= reserve:
        codes.append('ram_reserve')
    if observed['cpu_percent'] is None:
        codes.append('cpu_unknown')
    elif observed['cpu_percent'] >= ceiling:
        codes.append('cpu_busy')
    if observed['plugged_in'] is None:
        codes.append('power_unknown')
    elif observed['plugged_in'] is False:
        codes.append('battery_budget_unknown')
    for name in ('low_memory', 'thermal_distress'):
        if observed[name] is True:
            codes.append(name)
    if not slot_available:
        codes.append('model_slot_busy')
    return codes


def _mode_codes(mode):
    if mode is None:
        return ['mode_unknown']
    _keys(mode, {'requested', 'effective', 'dwell_pending'}, 'specialist mode')
    if (mode['requested'] is not None and (type(mode['requested']) is not str or mode['requested'] not in Scheduler.MODES)
            or type(mode['effective']) is not str or mode['effective'] not in Scheduler.MODES
            or type(mode['dwell_pending']) is not bool):
        raise ValueError('Invalid specialist mode evidence')
    if mode['requested'] is None and mode['effective'] == 'Idle' and not mode['dwell_pending']:
        raise ValueError('Automatic foreground Idle requires pending dwell')
    if not mode['dwell_pending'] and mode['effective'] not in {mode['requested'] or 'Balanced', 'Battery Saver', 'Critical Recovery'}:
        raise ValueError('Mode evidence contradicts completed foreground selection')
    # More restrictive owner intent applies immediately even during display dwell.
    if mode['requested'] in ('Idle', 'Battery Saver', 'Critical Recovery'):
        return [{'Idle':'mode_idle','Battery Saver':'mode_battery','Critical Recovery':'mode_critical'}[mode['requested']]]
    # Automatic foreground work may still display the prior Idle during dwell.
    # This exception never applies to an explicit Idle preference or recovery.
    automatic_idle = mode['requested'] is None and mode['effective'] == 'Idle' and mode['dwell_pending']
    if mode['effective'] == 'Idle' and not automatic_idle:
        return ['mode_idle']
    return {'Battery Saver': ['mode_battery'], 'Critical Recovery': ['mode_critical']}.get(mode['effective'], [])


def admit(app, *, slot_available=True):
    """Observe again before each role; never substitute a project-test RAM floor."""
    resources = service(app)
    policy = resources.scheduler.policy
    reserve, ceiling = policy.reserve_ram_gib, policy.cpu_ceiling
    if not _number(reserve) or not _number(ceiling, 100) or ceiling == 0:
        raise ValueError('Invalid specialist resource policy.')
    try:
        snapshot = resources.specialist_snapshot()
        raw, mode = snapshot['observations'], snapshot['mode']
    except (OSError, ValueError, AttributeError, TypeError):
        raw, mode = {}, None
    if not isinstance(raw, dict):
        raw = {}
    observed = _observations(raw)
    codes = _codes(observed, reserve, ceiling, slot_available) + _mode_codes(mode)
    timestamp = raw.get('observed_at')
    try:
        timestamp = _text(timestamp, 'observation time', 80)
    except ValueError:
        timestamp = None
    return validate(dict(schema_version=2, mode=mode, decision='defer' if codes else 'run_local',
        reason_codes=codes, reasons=[_REASONS[code] for code in codes], observed_at=timestamp,
        observations=observed, reserve_ram_gib=reserve, cpu_ceiling=ceiling,
        max_model_concurrency=1, model_budget='runtime_profile_and_context',
        slot_available=slot_available))


def validate(value):
    """Validate the bounded saved admission snapshot without sampling again."""
    version = value.get('schema_version') if type(value) is dict else None
    _keys(value, _KEYS | ({'mode'} if version == 2 else set()), 'specialist resources')
    _json_bytes(value, 8192)
    if (type(value['schema_version']) is not int or value['schema_version'] not in (1, 2)
            or type(value['max_model_concurrency']) is not int or value['max_model_concurrency'] != 1
            or value['model_budget'] != 'runtime_profile_and_context'
            or type(value['slot_available']) is not bool):
        raise ValueError('Invalid specialist resource scope.')
    _keys(value['observations'], _OBSERVATIONS, 'specialist observations')
    if value['observations'] != _observations(value['observations']):
        raise ValueError('Invalid specialist observation bounds.')
    reserve, ceiling = value['reserve_ram_gib'], value['cpu_ceiling']
    if not _number(reserve) or not _number(ceiling, 100) or ceiling == 0:
        raise ValueError('Invalid specialist resource limits.')
    if value['observed_at'] is not None:
        _text(value['observed_at'], 'observation time', 80)
    codes = _codes(value['observations'], reserve, ceiling, value['slot_available'])
    if version == 2:
        codes += _mode_codes(value['mode'])
    if (value['reason_codes'] != codes or value['reasons'] != [_REASONS[code] for code in codes]
            or value['decision'] != ('defer' if codes else 'run_local')):
        raise ValueError('Specialist admission disagrees with its observations.')
    return deepcopy(value)
