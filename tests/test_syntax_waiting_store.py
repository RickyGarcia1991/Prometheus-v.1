import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from prometheus_assistant.remote_job_protocol import normalize_request
from prometheus_assistant.syntax_waiting_store import WaitingStore


OWNER = 'a' * 64


def record(job='b' * 32):
    request = normalize_request(dict(schema_version=1, job_id=job, device_id='surface',
        worker_digest='c' * 64, operation='python-syntax-check', timeout_seconds=5,
        files=[dict(path='example.py', content='answer = 42\n')]))
    return dict(request=request,
        target=dict(account_id='account', device_id='surface', device_name='Surface',
                    worker_digest='c' * 64, online=True, configuration_digest='d' * 64),
        policy_revision='e' * 64, approval_id='f' * 32, fingerprint='1' * 64,
        approved_at=100, expires_at=200, placement=dict(mode='automatic', selected='surface'))


def test_approved_wait_persists_and_claim_never_replays(tmp_path):
    path = tmp_path / 'queue.sqlite3'
    original = record()
    first = WaitingStore(path, OWNER).enqueue(original, now=100)
    assert first['state'] == 'waiting'
    first['request']['files'][0]['content'] = 'changed'
    store = WaitingStore(path, OWNER)
    assert store.get(original['request']['job_id'])['request'] == original['request']
    claim = store.claim(original['request']['job_id'], now=101)
    assert claim['claimed'] is True
    assert claim['state'] == 'claimed'
    restarted = WaitingStore(path, OWNER)
    assert restarted.get(original['request']['job_id'])['state'] == 'unconfirmed'
    assert restarted.claim(original['request']['job_id'], now=102)['claimed'] is False
    assert restarted.enqueue(copy.deepcopy(original), now=103)['state'] == 'unconfirmed'


def outcome(raw, state='completed', passed=True):
    request = raw['request']
    receipt = {**{key: request[key] for key in ('schema_version', 'job_id', 'device_id', 'worker_digest', 'request_digest')},
               'state': state, 'checks': [dict(path='example.py', passed=passed)]}
    return dict(state=state, receipt=receipt, reasons=[])


def test_missing_reads_and_writes_are_inert(tmp_path):
    path = tmp_path / 'missing' / 'queue.db'
    store = WaitingStore(path, OWNER)
    assert store.get('b' * 32) is None and store.list() == [] and store.expire(now=100) == []
    for operation in (lambda: store.cancel('b' * 32, now=100), lambda: store.claim('b' * 32, now=100),
                      lambda: store.finish('b' * 32, outcome(record()), now=100),
                      lambda: store.update_wait('b' * 32, [], now=100, next_attempt_at=101)):
        with pytest.raises(ValueError):
            operation()
    assert not path.parent.exists()


def test_exact_idempotent_enqueue_and_detached_ordered_list(tmp_path):
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    first, second = record('2' * 32), record('3' * 32)
    assert store.enqueue(first, now=100) == store.enqueue(copy.deepcopy(first), now=100)
    store.enqueue(second, now=100)
    listed = store.list()
    assert [row['request']['job_id'] for row in listed] == ['2' * 32, '3' * 32]
    listed[0]['placement']['selected'] = 'changed'
    assert store.list()[0]['placement']['selected'] == 'surface'


@pytest.mark.parametrize('mutation', ['source', 'target', 'policy', 'fingerprint', 'expiry', 'placement'])
def test_changed_approval_cannot_replace_waiting_record(tmp_path, mutation):
    original = record()
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    store.enqueue(original, now=100)
    changed = copy.deepcopy(original)
    if mutation == 'source':
        del changed['request']['request_digest']
        changed['request']['files'][0]['content'] = 'different = True'
    elif mutation == 'target':
        changed['target']['account_id'] = 'another-account'
    elif mutation == 'policy':
        changed['policy_revision'] = '2' * 64
    elif mutation == 'fingerprint':
        changed['fingerprint'] = '2' * 64
    elif mutation == 'expiry':
        changed['expires_at'] = 201
    else:
        changed['placement']['selected'] = 'another-target'
    with pytest.raises(ValueError, match='another exact approval'):
        store.enqueue(changed, now=101)
    assert store.get(original['request']['job_id'])['request'] == original['request']


def test_waiting_retry_due_cancel_and_expire(tmp_path):
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    raw = record()
    job = raw['request']['job_id']
    store.enqueue(raw, now=100)
    row = store.update_wait(job, ['External power is required.'], now=101, next_attempt_at=120)
    assert row['next_attempt_at'] == 120
    assert store.claim(job, now=119)['claimed'] is False
    assert store.cancel(job, now=119)['state'] == 'cancelled'
    assert store.claim(job, now=120)['state'] == 'cancelled'
    other = record('3' * 32)
    store.enqueue(other, now=120)
    assert store.claim(other['request']['job_id'], now=200)['state'] == 'expired'
    assert store.enqueue(other, now=201)['state'] == 'expired'


def test_claimed_job_cannot_be_cancelled_or_requeued(tmp_path):
    raw = record()
    job = raw['request']['job_id']
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    store.enqueue(raw, now=100)
    assert store.claim(job, now=101)['claimed'] is True
    assert store.cancel(job, now=102)['state'] == 'unconfirmed'
    assert store.update_wait(job, ['No retry.'], now=103, next_attempt_at=104)['state'] == 'unconfirmed'
    assert store.claim(job, now=104)['claimed'] is False


def test_retry_deadline_is_clamped_and_mutating_reads_are_detached(tmp_path):
    raw = record()
    job = raw['request']['job_id']
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    store.enqueue(raw, now=100)
    result = store.update_wait(job, ['Waiting for power.'], now=190, next_attempt_at=220)
    assert result['next_attempt_at'] == 200
    result['reasons'].append('not persisted')
    assert store.get(job)['reasons'] == ['Waiting for power.']
    assert store.claim(job, now=199)['claimed'] is False
    assert store.expire(now=200)[0]['state'] == 'expired'


def test_unclaimed_and_malformed_results_never_advance_state(tmp_path):
    raw = record()
    job = raw['request']['job_id']
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    store.enqueue(raw, now=100)
    with pytest.raises(ValueError): store.finish(job, outcome(raw), now=101)
    assert store.get(job)['state'] == 'waiting'
    store.claim(job, now=102)
    for value in (None, {}, dict(state=[]), dict(state='unconfirmed', receipt=outcome(raw)['receipt'])):
        with pytest.raises(ValueError): store.finish(job, value, now=103)
    assert store.get(job)['state'] == 'unconfirmed'


def test_clock_rollback_permanently_expires_waiting_grants(tmp_path):
    path = tmp_path / 'queue.db'
    store = WaitingStore(path, OWNER)
    raw = record()
    store.enqueue(raw, now=100)
    store.expire(now=150)
    assert WaitingStore(path, OWNER).claim(raw['request']['job_id'], now=149)['state'] == 'expired'
    assert store.claim(raw['request']['job_id'], now=151)['claimed'] is False
    new = record('3' * 32)
    new['approved_at'] = 140
    assert store.enqueue(new, now=149)['state'] == 'expired'
    assert store.claim(new['request']['job_id'], now=160)['claimed'] is False


@pytest.mark.parametrize('state,passed', [('completed', True), ('completed', False), ('failed', False)])
def test_uncertain_claim_recovery_can_finish_once(tmp_path, state, passed):
    raw = record()
    job = raw['request']['job_id']
    path = tmp_path / 'queue.db'
    store = WaitingStore(path, OWNER)
    store.enqueue(raw, now=100)
    store.claim(job, now=101)
    store.finish(job, dict(state='unconfirmed', receipt=None), now=102)
    store = WaitingStore(path, OWNER)
    result = outcome(raw, state, passed)
    finished = store.finish(job, result, now=250)
    assert finished['state'] == state and finished['result'] == result
    assert store.finish(job, copy.deepcopy(result), now=251) == finished
    assert store.claim(job, now=252)['claimed'] is False
    changed = copy.deepcopy(result)
    changed['receipt']['checks'][0]['passed'] = not passed
    with pytest.raises(ValueError, match='replaced'):
        store.finish(job, changed, now=253)
    assert store.get(job)['result'] == result


@pytest.mark.parametrize('mutation', ['wrong_job', 'wrong_digest', 'coverage', 'nonterminal', 'state', 'extra_receipt'])
def test_invalid_receipt_cannot_finish_claim(tmp_path, mutation):
    raw = record()
    job = raw['request']['job_id']
    store = WaitingStore(tmp_path / 'queue.db', OWNER)
    store.enqueue(raw, now=100)
    store.claim(job, now=101)
    result = outcome(raw)
    if mutation == 'wrong_job': result['receipt']['job_id'] = '3' * 32
    elif mutation == 'wrong_digest': result['receipt']['request_digest'] = '3' * 64
    elif mutation == 'coverage': result['receipt']['checks'] = []
    elif mutation == 'nonterminal': result['receipt']['state'] = 'running'
    elif mutation == 'state': result['state'] = 'failed'
    else: result['receipt']['extra'] = True
    with pytest.raises(ValueError):
        store.finish(job, result, now=102)
    assert store.get(job)['state'] == 'unconfirmed'


@pytest.mark.parametrize('mutation', ['extra', 'owner', 'target', 'online', 'ttl', 'time', 'size', 'nested', 'nonfinite'])
def test_malformed_grants_never_create_storage(tmp_path, mutation):
    path = tmp_path / 'queue.db'
    raw = record()
    if mutation == 'extra': raw['extra'] = True
    elif mutation == 'owner': raw['approval_id'] = 'bad'
    elif mutation == 'target': raw['target']['device_id'] = 'different'
    elif mutation == 'online': raw['target']['online'] = False
    elif mutation == 'ttl': raw['expires_at'] = 3701
    elif mutation == 'time': raw['approved_at'] = True
    elif mutation == 'size':
        del raw['request']['request_digest']
        raw['request']['files'][0]['content'] = 'x' * 65536
    elif mutation == 'nested':
        child = raw['placement']
        for _ in range(20): child['child'] = {}; child = child['child']
    else: raw['placement']['value'] = float('nan')
    with pytest.raises(ValueError):
        WaitingStore(path, OWNER).enqueue(raw, now=100)
    assert not path.exists()


@pytest.mark.parametrize('mutation', ['bytes', 'version', 'table', 'column', 'trigger', 'owner', 'record', 'reason', 'state', 'result'])
def test_corruption_fails_closed_without_modifying_storage(tmp_path, mutation):
    path = tmp_path / 'queue.db'
    store = WaitingStore(path, OWNER)
    raw = record()
    store.enqueue(raw, now=100)
    if mutation == 'bytes': path.write_bytes(b'not sqlite')
    else:
        with sqlite3.connect(path) as db:
            if mutation == 'version': db.execute('PRAGMA user_version=999')
            elif mutation == 'table': db.execute('CREATE TABLE foreign_table(value TEXT)')
            elif mutation == 'column': db.execute('ALTER TABLE jobs ADD COLUMN extra TEXT')
            elif mutation == 'trigger': db.execute('CREATE TRIGGER extra AFTER INSERT ON jobs BEGIN DELETE FROM jobs; END')
            elif mutation == 'owner': db.execute("UPDATE metadata SET owner='different'")
            elif mutation == 'record': db.execute("UPDATE jobs SET record='{}'")
            elif mutation == 'reason': db.execute("UPDATE jobs SET reasons='[1]'")
            elif mutation == 'state': db.execute("UPDATE jobs SET state='anything'")
            else: db.execute("UPDATE jobs SET result='{}'")
    before = path.read_bytes()
    for operation in (store.list, lambda: store.claim(raw['request']['job_id'], now=101),
                      lambda: store.enqueue(raw, now=101)):
        with pytest.raises(ValueError): operation()
    assert path.read_bytes() == before


def test_cross_owner_isolation_and_bounded_capacity(tmp_path, monkeypatch):
    from prometheus_assistant import syntax_waiting_store as module
    path = tmp_path / 'queue.db'
    raw = record()
    store = WaitingStore(path, OWNER)
    store.enqueue(raw, now=100)
    other = WaitingStore(path, '2' * 64)
    for operation in (other.list, lambda: other.enqueue(raw, now=101),
                      lambda: other.claim(raw['request']['job_id'], now=101)):
        with pytest.raises(ValueError, match='another owner'): operation()
    monkeypatch.setattr(module, 'MAX_JOBS', 1)
    with pytest.raises(ValueError, match='capacity'):
        store.enqueue(record('3' * 32), now=101)
    assert len(store.list()) == 1
    monkeypatch.setattr(module, 'MAX_DATABASE_BYTES', path.stat().st_size - 1)
    with pytest.raises(ValueError): store.list()


def test_database_page_limit_rolls_back_new_grant(tmp_path, monkeypatch):
    from prometheus_assistant import syntax_waiting_store as module
    path = tmp_path / 'queue.db'
    store = WaitingStore(path, OWNER)
    store.enqueue(record(), now=100)
    monkeypatch.setattr(module, 'MAX_DATABASE_BYTES', path.stat().st_size)
    large = record('3' * 32)
    del large['request']['request_digest']
    large['request']['files'][0]['content'] = 'x' * 40000
    with pytest.raises(ValueError): store.enqueue(large, now=101)
    assert store.get('3' * 32) is None
    assert path.stat().st_size <= module.MAX_DATABASE_BYTES


def test_absolute_path_owner_and_link_guards(tmp_path):
    with pytest.raises(ValueError): WaitingStore('relative.db', OWNER)
    with pytest.raises(ValueError): WaitingStore(tmp_path / 'queue.db', 'bad-owner')
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'linked'
    try: link.symlink_to(target, target_is_directory=True)
    except OSError: pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(ValueError): WaitingStore(link / 'queue.db', OWNER)
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = tmp_path / ('queue.db' + suffix)
        sidecar.symlink_to(target / 'file')
        with pytest.raises(ValueError): WaitingStore(tmp_path / 'queue.db', OWNER)
        sidecar.unlink()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction qualification')
def test_native_windows_junction_is_rejected_without_symlink_privilege(tmp_path):
    target, link = tmp_path / 'target', tmp_path / 'junction'
    target.mkdir()
    env = dict(os.environ, PROMETHEUS_QUEUE_TEST_LINK=str(link), PROMETHEUS_QUEUE_TEST_TARGET=str(target))
    made = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        'New-Item -ItemType Junction -Path $env:PROMETHEUS_QUEUE_TEST_LINK -Target $env:PROMETHEUS_QUEUE_TEST_TARGET -ErrorAction Stop | Out-Null'],
        env=env, capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert made.returncode == 0, made.stderr.decode(errors='replace')
    try:
        with pytest.raises(ValueError, match='reparse'):
            WaitingStore(link / 'queue.db', OWNER)
    finally:
        link.rmdir()


def test_threaded_claim_has_one_winner(tmp_path):
    path = tmp_path / 'queue.db'
    raw = record()
    WaitingStore(path, OWNER).enqueue(raw, now=100)
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return WaitingStore(path, OWNER).claim(raw['request']['job_id'], now=101)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(row['claimed'] for row in results) == 1


@pytest.mark.parametrize('existing', [False, True])
def test_independent_process_enqueue_claim_and_crash_is_once_only(tmp_path, existing):
    path = tmp_path / 'queue.db'
    raw = record()
    if existing: WaitingStore(path, OWNER).enqueue(raw, now=100)
    code = '''import json,os,sys
from prometheus_assistant.syntax_waiting_store import WaitingStore
raw=json.loads(sys.stdin.readline())
store=WaitingStore(sys.argv[1],sys.argv[2])
store.enqueue(raw,now=100)
row=store.claim(raw['request']['job_id'],now=100)
print(json.dumps(row),flush=True)
os._exit(0)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    processes = []
    try:
        for _ in range(2):
            processes.append(subprocess.Popen([sys.executable, '-B', '-c', code, str(path), OWNER],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        for process in processes:
            process.stdin.write((json.dumps(raw) + '\n').encode()); process.stdin.flush()
        rows = []
        for process in processes:
            output, error = process.communicate(timeout=15)
            assert process.returncode == 0, error.decode()
            rows.append(json.loads(output))
        assert sum(row['claimed'] for row in rows) == 1
        assert WaitingStore(path, OWNER).get(raw['request']['job_id'])['state'] == 'unconfirmed'
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
