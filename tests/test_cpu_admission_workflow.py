"""CPU history affects actual owner review/application, with generated fixtures."""
from types import SimpleNamespace
import pytest

from test_project_tasks_ui import setup, decide, OWNER, BINDING


def seed_heavy(s, checks):
    candidate = {'project': str(s.project), 'checks': checks}
    rows = []
    for check in checks:
        rows.append({**check, 'passed': True, 'cancelled': False, 'timed_out': False,
            'exit_code': 0, 'resource_measurements': {'scope': 'direct_process',
                'ram_gib': .25, 'cpu_seconds': 40}})
    for _ in range(5):
        assert s.app.project_resources.observe(candidate, {'status': 'passed', 'checks': rows})['recorded']


def cpu(s, percentage):
    s.app.project_resources.probe = SimpleNamespace(snapshot=lambda **_: dict(
        available_ram_gib=8, total_ram_gib=16, free_disk_gib=20,
        cpu_percent=percentage, battery_percent=100, plugged_in=True,
        observed_at='LABELED SYNTHETIC LOAD FOR WORKFLOW TEST'))


def test_fresh_cpu_pressure_after_owner_review_prevents_edit(setup):
    s = setup
    cold = s.run(s.req)
    checks = cold['details']['approval']['review']['checks']
    decide(s, cold, 'deny')
    seed_heavy(s, checks)
    cpu(s, 10)
    reviewed = s.run(s.req)
    assert reviewed['details']['approval']['review']['resource_assessment']['decision'] == 'run_local'
    cpu(s, 70)
    with pytest.raises(ValueError, match='deferred before changing files'):
        decide(s, reviewed)
    assert 'return 1' in s.file.read_text()
    assert not s.app.project_resources.scheduler.leases
    assert s.app.action_approvals.pending(owner=OWNER, binding=BINDING) is None


def test_busy_review_discloses_defer_then_fresh_headroom_allows_native_check(setup):
    s = setup
    cold = s.run(s.req)
    checks = cold['details']['approval']['review']['checks']
    decide(s, cold, 'deny')
    seed_heavy(s, checks)
    cpu(s, 70)
    reviewed = s.run(s.req)
    assessment = reviewed['details']['approval']['review']['resource_assessment']
    assert assessment['decision'] == 'defer'
    assert 'return 1' in s.file.read_text()
    cpu(s, 10)
    result = decide(s, reviewed)['details']['project_edit']
    assert result['status'] == 'passed'
    assert 'return 2' in s.file.read_text()
    assert result['resource_assessment']['decision'] == 'run_local'
    assert not s.app.project_resources.scheduler.leases
