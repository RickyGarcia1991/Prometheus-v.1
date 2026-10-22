"""Read-only planner cannot imply activation or mutate existing task definitions."""
import copy
import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / 'tools/Prometheus.HostAgent/plan-quiet-host-enrollment.py'
spec = importlib.util.spec_from_file_location('enrollment_plan', FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ROOT = r'C:\Generated\Prometheus'


def test_seventeen_unrelated_tasks_propose_only_reconnect_without_mutation():
    inventory = {'complete': True, 'tasks': [{'name': f'Development-{n}', 'path': '\\', 'actions': ['preserve']} for n in range(17)]}
    before = copy.deepcopy(inventory)
    result = module.plan(inventory, ROOT)
    assert inventory == before
    assert not result['blockers']
    assert result['observed_task_count'] == 17
    assert [t['name'] for t in result['create_tasks']] == [module.REQUIRED]
    assert result['create_tasks'][0]['executable'] == ROOT + '\\Prometheus-QuietHost-v3.exe'
    assert len(result['optional_modes_not_enrolled']) == 7
    assert result['activation_ready'] is False
    assert result['start_tasks'] == result['change_existing_tasks'] == []
    assert len(result['activation_gates']) >= 7


@pytest.mark.parametrize('name', list(module.MODES))
@pytest.mark.parametrize('actions', [[], ['foreign'], ['owned','extra']])
def test_existing_names_always_refused_without_guessed_ownership(name, actions):
    result = module.plan({'complete': True, 'tasks': [{'name': name.upper(), 'path': '\\Other\\', 'actions': actions}]}, ROOT)
    assert result['blockers'] and not result['create_tasks']
    assert not result['change_existing_tasks'] and not result['start_tasks']


@pytest.mark.parametrize('inventory', [{}, {'tasks': []}, {'complete': False,'tasks': []}, {'complete':True,'tasks':[{}]}, {'complete':True,'tasks':None}])
def test_incomplete_or_malformed_inventory_cannot_mean_absent(inventory):
    result = module.plan(inventory, ROOT)
    assert result['blockers'] and not result['create_tasks'] and not result['activation_ready']


def test_optional_all_is_explicitly_gated():
    result = module.plan({'complete':True,'tasks':[]}, ROOT, 'optional-all')
    assert result['blockers'] and not result['create_tasks']
    assert 'not specified' in result['blockers'][0]


@pytest.mark.parametrize('root', ['relative', r'\\server\share', r'C:\some\..\Prometheus'])
def test_ambiguous_roots_refused(root):
    with pytest.raises(ValueError):
        module.plan({'complete': True,'tasks':[]}, root)
