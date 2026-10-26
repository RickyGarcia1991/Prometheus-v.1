"""Isolated API fault injection; creates no Scheduler task or native process."""
import active_migration_fixture_provider as p


def test_probe_close_failure_is_reported_and_other_records_attempted(tmp_path, monkeypatch):
    rows = {1: dict(pid=1, created='one'), 2: dict(pid=2, created='two')}
    for pid in rows:
        (tmp_path / f'record-{pid}.json').write_text('{}')
    monkeypatch.setattr(p, 'load', lambda path: dict(token='scope', owner='owner') if path.name == 'task-spec.json' else rows[int(path.stem.split('-')[-1])])
    monkeypatch.setattr(p, 'native', lambda root, mode: dict(processes=[]) if mode == 'CleanupDisable' else dict(removed=True))
    opened = []
    closed = []
    monkeypatch.setattr(p.q, 'Open', lambda rights, inherit, pid: opened.append(pid) or pid)
    monkeypatch.setattr(p.q, 'Wait', lambda handle, timeout: 0)
    monkeypatch.setattr(p.q, 'Close', lambda handle: closed.append(handle) or handle != 1)
    result = p.cleanup(tmp_path)
    assert opened == [1, 2] and closed == [1, 2]
    assert result['task_removed'] and not result['passed']
    assert len(result['errors']) == 1 and 'CloseHandle' in result['errors'][0]
