import hashlib
import threading

import pytest

from prometheus_assistant.reviewer_check_capture import ApprovedCheckCapture


class Workspace:
    def __init__(self, body=b'x = 1\n'):
        self.body, self.calls = body, 0
        self.lock = threading.RLock()
        self.result = dict(argv=['python', 'python-compile', 'a.py'], exit_code=0,
                           cancelled=False, timed_out=False, duration_seconds=.1,
                           output='arbitrary stdout cannot create evidence')

    def read_file(self, path):
        return {'sha256': hashlib.sha256(self.body).hexdigest()}

    def _command(self, preset, target):
        return ['python', preset, target]

    def run_tests(self, preset, target, **options):
        self.calls += 1
        return self.result


def capture(workspace, checks=None):
    checks = checks if checks is not None else [{'preset': 'python-compile', 'target': 'a.py'}]
    candidate = dict(version=1, path='a.py', after_sha256=hashlib.sha256(workspace.body).hexdigest(), checks=checks)
    return ApprovedCheckCapture(candidate, {'a.py': workspace.body})


def test_exact_native_capture_and_detached_replay():
    workspace = Workspace()
    measurement = capture(workspace)
    result = measurement.run_check(workspace, 'python-compile', 'a.py')
    assert result is workspace.result
    packet = measurement.finish()
    assert packet['complete'] and not packet['semantic_evidence_available']
    assert not packet['authorized'] and not packet['production_accepted']
    row = next(iter(packet['evidence'].values()))['checks'][0]
    assert row['input']['kind'] == 'process_outcome'
    assert row['passed'] is True
    assert 'output' not in str(row)
    workspace.body = b'rollback\n'
    packet['files']['a.py'] = b'tampered'
    assert measurement.finish()['files']['a.py'] == b'x = 1\n'
    assert workspace.calls == 1
    with pytest.raises(ValueError):
        measurement.run_check(workspace, 'python-compile', 'a.py')


@pytest.mark.parametrize('changes', [dict(exit_code=1), dict(exit_code=None, cancelled=True),
                                     dict(exit_code=1, timed_out=True)])
def test_failure_timeout_cancel_are_native_failed_evidence(changes):
    workspace = Workspace()
    workspace.result.update(changes)
    measurement = capture(workspace)
    measurement.run_check(workspace, 'python-compile', 'a.py')
    packet = measurement.finish()
    assert packet['complete']
    assert next(iter(packet['evidence'].values()))['checks'][0]['passed'] is False


@pytest.mark.parametrize('changes', [dict(argv=['other']), dict(exit_code=True),
    dict(cancelled=0), dict(timed_out=None), dict(duration_seconds=float('nan'))])
def test_malformed_native_data_is_unavailable(changes):
    workspace = Workspace()
    workspace.result.update(changes)
    measurement = capture(workspace)
    measurement.run_check(workspace, 'python-compile', 'a.py')
    packet = measurement.finish()
    assert not packet['complete']
    assert packet['issues'][0]['reason'] == 'invalid_native_result'


def test_changed_snapshot_blocks_execution():
    workspace = Workspace()
    measurement = capture(workspace)
    workspace.body = b'changed\n'
    with pytest.raises(ValueError):
        measurement.run_check(workspace, 'python-compile', 'a.py')
    assert workspace.calls == 0
    assert not measurement.finish()['complete']


def test_changed_during_execution_rejects_capture():
    workspace = Workspace()
    measurement = capture(workspace)
    original = workspace.run_tests
    def change(*args, **kwargs):
        workspace.body = b'changed\n'
        return original(*args, **kwargs)
    workspace.run_tests = change
    measurement.run_check(workspace, 'python-compile', 'a.py')
    assert measurement.finish()['issues'][0]['reason'] == 'snapshot_changed_during_check'


def test_missing_unplanned_and_repeated_checks_do_not_execute():
    workspace = Workspace()
    measurement = capture(workspace)
    with pytest.raises(ValueError):
        measurement.run_check(workspace, 'node-test', 'a.py')
    assert workspace.calls == 0
    assert not measurement.finish()['complete']
    measurement = capture(workspace)
    measurement.run_check(workspace, 'python-compile', 'a.py')
    with pytest.raises(ValueError):
        measurement.run_check(workspace, 'python-compile', 'a.py')
    assert workspace.calls == 1


def test_native_real_compile_does_not_execute_submitted_code(tmp_path):
    from prometheus_assistant.coding_workspace import CodingWorkspace
    root = tmp_path / 'project'
    root.mkdir()
    source = b'raise RuntimeError("must not execute")\n'
    (root / 'a.py').write_bytes(source)
    workspace = CodingWorkspace(root, tmp_path / 'history', resources=tmp_path / 'resources')
    candidate = dict(version=1, path='a.py', after_sha256=hashlib.sha256(source).hexdigest(),
                     checks=[{'preset': 'python-compile', 'target': 'a.py'}])
    measurement = ApprovedCheckCapture(candidate, {'a.py': source})
    result = measurement.run_check(workspace, 'python-compile', 'a.py')
    assert result['exit_code'] == 0
    assert measurement.finish()['complete']


def test_runner_error_stays_unavailable_and_no_retry():
    workspace = Workspace()
    measurement = capture(workspace)
    def fail(*args, **kwargs):
        raise OSError('not runnable')
    workspace.run_tests = fail
    with pytest.raises(OSError):
        measurement.run_check(workspace, 'python-compile', 'a.py')
    with pytest.raises(ValueError):
        measurement.run_check(workspace, 'python-compile', 'a.py')
    assert measurement.finish()['issues'][0]['reason'] == 'runner_error'


def test_fresh_run_identity_and_original_snapshot_are_bound():
    workspace = Workspace()
    first, second = capture(workspace), capture(workspace)
    first.run_check(workspace, 'python-compile', 'a.py')
    second.run_check(workspace, 'python-compile', 'a.py')
    a, b = first.finish(), second.finish()
    assert a['candidate_sha256'] == b['candidate_sha256']
    assert a['run_id'] != b['run_id']
    from prometheus_assistant.reviewer_check_evidence import measured_checks
    assert measured_checks(a['evidence'], a['files'])[0]['evidence_id'] != measured_checks(b['evidence'], b['files'])[0]['evidence_id']


def test_duplicate_source_digest_explicitly_unavailable():
    workspace = Workspace()
    digest = hashlib.sha256(workspace.body).hexdigest()
    checks = [{'preset': 'python-compile', 'target': 'a.py'}]
    members = [dict(version=1, path=path, after_sha256=digest, checks=checks) for path in ('a.py', 'b.py')]
    measurement = ApprovedCheckCapture(dict(version=2, candidates=members, checks=checks),
                                       {'a.py': workspace.body, 'b.py': workspace.body})
    measurement.run_check(workspace, 'python-compile', 'a.py')
    result = measurement.finish()
    assert result['evidence'] == {} and result['files'] == {}
    assert not result['complete']
    assert result['issues'][0]['reason'] == 'duplicate_source_digest'
