import copy
import json
import subprocess
import sys

import pytest

from prometheus_assistant.remote_job_protocol import normalize_request, validate_receipt
from prometheus_assistant.remote_syntax_worker import run_job, run_signed_job, execute_linux_syntax, _CHILD_CODE


def request():
    return dict(schema_version=1, job_id='c'*32, device_id='surface', worker_digest='d'*64,
                operation='python-syntax-check', timeout_seconds=10,
                files=[dict(path='main.py', content='answer = 42\n')])


def facts():
    return dict(enabled=True, device_id='surface', worker_digest='d'*64, observed_at=100,
                available_ram_gib=2, free_disk_gib=5, cpu_percent=10, plugged_in=True)


def receipt(req, passed=True):
    return {**{key: req[key] for key in ('schema_version', 'job_id', 'device_id',
            'worker_digest', 'request_digest')}, 'state': 'completed',
            'checks': [dict(path=f['path'], passed=passed) for f in req['files']]}


class Ledger:
    def __init__(self):
        self.value = None
        self.claims = 0

    def lookup(self, req):
        return copy.deepcopy(self.value)

    def claim(self, req):
        if self.value:
            return copy.deepcopy(self.value)
        self.claims += 1
        self.value = dict(claimed=False, state='unconfirmed', receipt=None)
        return dict(claimed=True, state='claimed', receipt=None)

    def finish(self, req, result):
        self.value = dict(claimed=False, state=result['state'], receipt=copy.deepcopy(result))


def test_success_and_duplicate_never_execute_twice():
    ledger, calls = Ledger(), []
    def execute(req):
        calls.append(req)
        return receipt(req)
    first = run_job(request(), ledger, facts, executor=execute, clock=lambda: 100)
    second = run_job(request(), ledger, lambda: pytest.fail('No new admission for replay'),
                     executor=lambda _: pytest.fail('Must not execute twice'))
    assert first['state'] == second['state'] == 'completed'
    assert first['evidence']['status'] == 'passed'
    assert second['replayed'] is True and first['replayed'] is False
    assert len(calls) == ledger.claims == 1


@pytest.mark.parametrize('changed,expected', [({'enabled': False}, 'rejected'),
    ({'plugged_in': False}, 'deferred'), ({'observed_at': 69}, 'deferred'),
    ({'device_id': 'wrong'}, 'rejected'), ({'cpu_percent': 90}, 'deferred')])
def test_local_admission_precedes_claim_and_execution(changed, expected):
    ledger = Ledger()
    result = run_job(request(), ledger, lambda: {**facts(), **changed},
                     executor=lambda _: pytest.fail('Refused work ran'), clock=lambda: 100)
    assert result['state'] == expected and result['reasons']
    assert ledger.claims == 0


def test_crash_after_claim_is_unconfirmed_and_not_replayed():
    ledger = Ledger()
    def execute(req):
        raise OSError('internal details should not escape')
    result = run_job(request(), ledger, facts, executor=execute, clock=lambda: 100)
    assert result['state'] == 'unconfirmed' and 'internal details' not in str(result)
    again = run_job(request(), ledger, facts, executor=lambda _: pytest.fail('Crash repeated'))
    assert again['state'] == 'unconfirmed' and ledger.claims == 1


def test_malformed_or_wrong_receipt_never_persists_success():
    ledger = Ledger()
    def execute(req):
        got = receipt(req)
        got['worker_digest'] = 'e'*64
        return got
    result = run_job(request(), ledger, facts, executor=execute, clock=lambda: 100)
    assert result['state'] == ledger.value['state'] == 'unconfirmed'


def test_failed_syntax_is_completed_evidence_failure():
    got = run_job(request(), Ledger(), facts, executor=lambda req: receipt(req, False),
                  clock=lambda: 100)
    assert got['state'] == 'completed' and got['evidence']['status'] == 'failed'


def test_receipt_store_failure_keeps_unconfirmed():
    ledger = Ledger()
    ledger.finish = lambda *_: (_ for _ in ()).throw(OSError('disk'))
    got = run_job(request(), ledger, facts, executor=receipt, clock=lambda: 100)
    assert got['state'] == 'unconfirmed'


def test_real_ledger_reopens_receipt_and_preserves_failed_claim(tmp_path):
    from prometheus_assistant.remote_job_store import JobStore
    path = tmp_path / 'jobs.sqlite3'
    got = run_job(request(), JobStore(path), facts, executor=receipt, clock=lambda: 100)
    assert got['evidence']['status'] == 'passed'
    again = run_job(request(), JobStore(path), facts,
                    executor=lambda _: pytest.fail('Durable job repeated'))
    assert again['receipt'] == got['receipt'] and again['replayed'] is True
    other = request()
    other['job_id'] = 'e'*32
    JobStore(path).claim(other)
    recovered = run_job(other, JobStore(path), facts,
                        executor=lambda _: pytest.fail('Uncertain job repeated'))
    assert recovered['state'] == 'unconfirmed'


def test_real_ledger_rejects_changed_content_before_process(tmp_path):
    from prometheus_assistant.remote_job_store import JobStore
    store = JobStore(tmp_path / 'jobs.sqlite3')
    run_job(request(), store, facts, executor=receipt, clock=lambda: 100)
    changed = request()
    changed['files'][0]['content'] = 'different = True'
    with pytest.raises(ValueError, match='different exact request'):
        run_job(changed, store, facts, executor=lambda _: pytest.fail('Changed source ran'))


def test_competing_claim_does_not_call_executor():
    ledger = Ledger()
    ledger.claim = lambda _: dict(claimed=False, state='unconfirmed', receipt=None)
    got = run_job(request(), ledger, facts, executor=lambda _: pytest.fail('Claim race ran twice'),
                  clock=lambda: 100)
    assert got['state'] == 'unconfirmed' and got['replayed']


def test_signed_job_checks_sender_before_local_actions(tmp_path):
    from prometheus_assistant.remote_job_envelope import seal_request
    from prometheus_assistant.remote_job_store import JobStore
    packet = seal_request(request(), lambda _: b'x'*256, issued_at=100, expires_at=120)
    path = tmp_path / 'jobs.sqlite3'
    with pytest.raises(ValueError, match='verification failed'):
        run_signed_job(packet, JobStore(path), lambda: pytest.fail('Unauthenticated admission'),
            lambda *_: False, device_id='surface', worker_digest='d'*64,
            executor=lambda _: pytest.fail('Unauthenticated execution'), clock=lambda:100)
    assert not path.exists()


def test_signed_job_reaches_bound_ledger_and_admission(tmp_path):
    from prometheus_assistant.remote_job_envelope import seal_request
    from prometheus_assistant.remote_job_store import JobStore
    # Callback double tests orchestration; native RSA-PSS qualification is separate.
    packet = seal_request(request(), lambda _: b'x'*256, issued_at=100, expires_at=120)
    store = JobStore(tmp_path / 'jobs.sqlite3')
    got = run_signed_job(packet, store, facts, lambda *_: True, device_id='surface',
                         worker_digest='d'*64, executor=receipt, clock=lambda:100)
    assert got['evidence']['status'] == 'passed'
    repeat = run_signed_job(packet, store, facts, lambda *_:True, device_id='surface',
        worker_digest='d'*64, executor=lambda _:pytest.fail('Replayed'), clock=lambda:101)
    assert repeat['replayed'] and repeat['receipt'] == got['receipt']


def test_native_platform_guard_before_claim():
    if sys.platform == 'linux':
        pytest.skip('This check covers the non-Linux rejection branch.')
    ledger = Ledger()
    got = run_job(request(), ledger, facts, clock=lambda: 100)
    assert got['state'] == 'deferred' and ledger.claims == 0
    with pytest.raises(RuntimeError):
        execute_linux_syntax(request())


def test_child_requires_linux_limits_even_when_directly_invoked():
    if sys.platform == 'linux':
        pytest.skip('Covered by native Linux qualification.')
    child = subprocess.run([sys.executable, '-I', '-S', '-c', _CHILD_CODE],
                           input=b'{}', capture_output=True, timeout=10)
    assert child.returncode != 0 and not child.stdout


@pytest.mark.skipif(sys.platform != 'linux', reason='Native Linux limit and compiler acceptance runs on the Surface.')
def test_native_compile_never_executes_source(tmp_path):
    marker = tmp_path / 'must-not-exist'
    raw = request()
    raw['files'] = [dict(path='main.py', content=f'open({str(marker)!r}, "w").write("bad")'),
                    dict(path='broken.py', content='if broken\n'),
                    dict(path='loop.py', content='while True: pass')]
    req = normalize_request(raw)
    got = execute_linux_syntax(req)
    assert not marker.exists()
    assert got['state'] == 'completed'
    assert [row['passed'] for row in got['checks']] == [True, False, True]
    assert validate_receipt(req, got)['status'] == 'failed'
