"""Application-bound approval, persistence, idle resume and legacy coexistence."""
from contextlib import contextmanager, nullcontext
import copy
import json
import time
from types import SimpleNamespace

import pytest

from prometheus_assistant import scheduled_syntax_ui as scheduled, ui_server as ui


def receipt(req):
    result={**{key: req[key] for key in ('schema_version', 'job_id', 'device_id',
        'worker_digest', 'request_digest')}, 'state': 'completed',
        'checks': [dict(path=row['path'], passed=True) for row in req['files']]}
    if req['schema_version']==2:
        result.update({key:req[key] for key in ('operation','tool','profile')})
        result.update(status='passed',diagnostics=[],reason='',source_unchanged=True)
    return result


def wait_for_job(app, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with app.lock:
            result = copy.deepcopy(app.jobs[job_id])
        if result['state'] != 'running':
            return result
        time.sleep(.01)
    pytest.fail('The bounded application job did not finish.')


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'hello.py').write_bytes(b'answer = 42\n')
    f = SimpleNamespace(project=project, memory=tmp_path/'data/memory.sqlite3',
        root=tmp_path/'host', owner='a'*64, binding='c'*64, valid=True,
        now=time.time(), calls=[], apps=[], local_ram=8, surface_ram=4,
        local_online=True, surface_online=True, local_power=True,
        local_error=None, remote_error=None)
    @contextmanager
    def owner():
        if not f.valid:
            raise ValueError('Owner is locked.')
        yield f.owner
    f.grant = owner
    def target(kind):
        if not getattr(f, kind+'_online'):
            raise ValueError('Destination is offline.')
        return dict(account_id='local' if kind == 'local' else 'account',
            device_id='dell' if kind == 'local' else 'surface',
            device_name='Dell' if kind == 'local' else 'Surface',
            worker_digest=('b' if kind == 'local' else 'e')*64,
            configuration_digest=('d' if kind == 'local' else 'f')*64, online=True)
    def admission(kind, req=None):
        return dict(decision='ready', reasons=[], authenticated=True,
            observed_at=f.now, expires_at=f.now+30,
            observations=dict(enabled=True, available_ram_gib=getattr(f, kind+'_ram'),
                free_disk_gib=10, cpu_percent=20, battery_percent=100,
                plugged_in=f.local_power if kind == 'local' else True))
    def local_execute(req):
        f.calls.append(('local', copy.deepcopy(req)))
        if f.local_error:
            raise f.local_error
        return receipt(req)
    def dispatch(envelope):
        req = envelope['payload']['request']
        f.calls.append(('surface', copy.deepcopy(req)))
        if f.remote_error:
            raise f.remote_error
        return receipt(req)
    def sign(_):
        f.calls.append(('sign', None))
        return b'x'*256
    def create_app():
        app = ui.Interface(f.memory)
        app._scheduled_syntax_clock = lambda: f.now
        app._local_syntax_adapter = SimpleNamespace(target=lambda: target('local'),
            admission=lambda req=None: admission('local', req), execute=local_execute,
            recover=lambda req: None)
        app._remote_worker_client = SimpleNamespace(root=f.root,
            target=lambda: target('surface'), admission=lambda req=None: admission('surface', req),
            signer=sign, dispatch=dispatch, clock=lambda: f.now,
            recover=lambda req, ledger: ledger.lookup(req))
        f.apps.append(app)
        f.app = app
        return app
    def submit(body):
        started = f.app.submit(body, grant_owner=f.grant, approval_binding=f.binding)
        return wait_for_job(f.app, started['id'])
    def run(body):
        result = submit(body)
        assert result['state'] == 'done', result
        return result['result']
    def prepare(mode='scheduled_syntax_prepare'):
        return run(dict(mode=mode, project=str(f.project), remote_files='hello.py'))['details']['approval']
    def decide(row, decision='approve'):
        return run(dict(mode='approval_decision', approval_id=row['id'],
            approval_fingerprint=row['fingerprint'], approval_decision=decision,
            session=row['session']))
    def status():
        return scheduled.status(f.app, grant_owner=f.grant, binding=f.binding)
    def restart():
        f.app.close()
        f.binding = '9'*64
        return create_app()
    f.submit, f.run, f.prepare, f.decide = submit, run, prepare, decide
    f.status, f.restart = status, restart
    f.store = lambda owner=None: scheduled.queue(f.app, owner or f.owner)
    monkeypatch.setattr(ui, 'reference_session', nullcontext)
    monkeypatch.setattr(scheduled, 'time', SimpleNamespace(time=lambda: f.now, monotonic=time.monotonic))
    create_app()
    yield f
    for app in f.apps:
        if not app.closing:
            app.close()


def wait_locally(f):
    row = f.prepare()
    assert row['review']['destination']['device_id'] == 'dell'
    assert row['review']['files'][0]['content'] == 'answer = 42\n'
    assert f.calls == []
    # Pressure appears after the exact destination was reviewed. It must wait
    # on that destination, even though the Surface still has ready headroom.
    f.local_ram = 1
    result = f.decide(row)['details']['scheduled_syntax']
    assert result['state'] == 'waiting' and f.calls == []
    return result['job_id']


def test_approve_wait_restart_same_owner_status_arms_idle_and_preserves_snapshot(fixture):
    f = fixture
    job_id = wait_locally(f)
    original = f.store().get(job_id)
    (f.project/'hello.py').write_text('different =\n', encoding='utf-8')
    f.restart()
    assert f.app.action_approvals.pending(owner=f.owner, binding=f.binding) is None
    assert f.store().get(job_id)['request'] == original['request']
    f.local_ram = 8
    f.now += 31
    assert scheduled.idle(f.app) is False and f.calls == []
    status = f.status()
    assert status['jobs'][0]['state'] == 'waiting'
    assert 'answer = 42' not in json.dumps(status)
    f.app.idle_maintenance()
    with f.app.lock:
        jobs = list(f.app.jobs.values())
    assert len(jobs) == 1 and jobs[0]['action'] == 'scheduled_syntax_tick'
    assert jobs[0]['background'] is True
    finished = wait_for_job(f.app, jobs[0]['id'])
    assert finished['state'] == 'done', finished
    saved = f.store().get(job_id)
    assert saved['state'] == 'completed'
    assert saved['result']['evidence']['status'] == 'passed'
    assert f.calls == [('local', original['request'])]
    assert f.calls[0][1]['files'][0]['content'] == 'answer = 42\n'
    f.app._scheduled_syntax_next = 0
    assert scheduled.idle(f.app) is False
    f.restart()
    assert f.status()['jobs'][0]['state'] == 'completed'
    assert scheduled.idle(f.app) is False and len(f.calls) == 1


def test_remote_lightweight_policy_waits_and_recovers_without_local_fallback(fixture):
    f=fixture
    row=f.run(dict(mode='scheduled_syntax_prepare',project=str(f.project),remote_files='hello.py',
        scheduled_placement='remote-lightweight-v1'))['details']['approval']
    assert row['review']['destination']['device_id']=='surface'
    assert row['review']['placement']['strategy']=='remote-lightweight-v1'
    f.surface_ram=.1
    waiting=f.decide(row)['details']['scheduled_syntax']
    assert waiting['state']=='waiting' and not f.calls
    job_id=waiting['job_id']
    original=f.store().get(job_id)['request']
    f.restart();f.now+=31
    f.run(dict(mode='scheduled_syntax_tick'))
    assert f.store().get(job_id)['state']=='waiting' and not f.calls
    f.surface_ram=4;f.now+=31
    f.run(dict(mode='scheduled_syntax_tick'))
    assert f.store().get(job_id)['state']=='completed'
    assert f.store().get(job_id)['request']==original
    assert f.calls==[('sign',None),('surface',original)]
    f.run(dict(mode='scheduled_syntax_recover',scheduled_job_id=job_id))
    assert f.calls==[('sign',None),('surface',original)]


def test_lint_approval_binds_tool_and_remote_only_waiting(fixture):
    f=fixture;tool=dict(sha256='1'*64,version='0.17.1')
    f.app._remote_worker_client.lint_tool=lambda:dict(tool)
    row=f.run(dict(mode='scheduled_syntax_prepare',project=str(f.project),remote_files='hello.py',
        scheduled_operation='python-ruff-check-v1'))['details']['approval']
    assert row['review']['tool']==tool
    assert row['review']['profile']=='ruff-python-v1'
    assert row['review']['destination']['device_id']=='surface'
    assert row['review']['placement']['strategy']=='remote-lightweight-v1'
    assert row['review']['resource_admission']['estimated_ram_gib']==.75
    result=f.decide(row)['details']['scheduled_syntax']
    assert result['state']=='completed'
    assert result['result']['receipt']['status']=='passed'
    assert [call[0] for call in f.calls]==['sign','surface']


def test_lint_cannot_use_legacy_worker_or_local_fallback(fixture):
    f=fixture
    result=f.submit(dict(mode='scheduled_syntax_prepare',project=str(f.project),remote_files='hello.py',
        scheduled_operation='python-ruff-check-v1'))
    assert result['state']!='done' and not f.calls


def test_locked_owner_cannot_inspect_resume_or_cancel_waiting_job(fixture):
    f = fixture
    job_id = wait_locally(f)
    f.local_ram = 8
    f.now += 31
    f.valid = False
    with pytest.raises(ValueError, match='locked'):
        f.status()
    assert scheduled.idle(f.app) is False
    result = f.submit(dict(mode='scheduled_syntax_cancel', scheduled_job_id=job_id))
    assert result['state'] == 'error' and 'locked' in result['error']
    assert f.store().get(job_id)['state'] == 'waiting' and f.calls == []


def test_other_owner_cannot_see_or_cancel_retained_job(fixture):
    f = fixture
    original_owner = f.owner
    job_id = wait_locally(f)
    f.owner = '7'*64
    f.binding = '8'*64
    assert f.status()['jobs'] == []
    assert scheduled.idle(f.app) is False
    result = f.submit(dict(mode='scheduled_syntax_cancel', scheduled_job_id=job_id))
    assert result['state'] == 'error'
    assert f.store(original_owner).get(job_id)['state'] == 'waiting' and f.calls == []


def test_cancelled_approved_wait_survives_restart_without_running(fixture):
    f = fixture
    job_id = wait_locally(f)
    result = f.run(dict(mode='scheduled_syntax_cancel', scheduled_job_id=job_id))
    assert result['details']['scheduled_syntax']['state'] == 'cancelled'
    f.restart()
    f.local_ram = 8
    f.now += 31
    assert f.status()['jobs'][0]['state'] == 'cancelled'
    assert scheduled.idle(f.app) is False and f.calls == []


def test_offline_exact_destination_waits_without_switching_or_claiming(fixture):
    f = fixture
    row = f.prepare()
    f.local_online = False
    result = f.decide(row)['details']['scheduled_syntax']
    assert result['state'] == 'waiting'
    saved = f.store().get(result['job_id'])
    assert saved['target']['device_id'] == 'dell'
    assert saved['next_attempt_at'] == f.now + 30 and f.calls == []
    f.now += 31
    f.local_online = True
    f.run(dict(mode='scheduled_syntax_tick'))
    assert f.store().get(result['job_id'])['state'] == 'completed'
    assert [kind for kind, _ in f.calls] == ['local']


def test_reject_never_publishes_waiting_authority(fixture):
    f = fixture
    row = f.prepare()
    result = f.decide(row, 'reject')['details']['scheduled_syntax']
    assert result['state'] == 'rejected'
    assert f.store().list() == [] and f.calls == []


def test_uncertain_local_completion_is_retained_and_never_replayed(fixture):
    f = fixture
    f.local_error = OSError('Simulated lost completion')
    result = f.decide(f.prepare())['details']['scheduled_syntax']
    assert result['state'] == 'unconfirmed' and len(f.calls) == 1
    f.restart()
    f.now += 31
    f.status()
    assert scheduled.idle(f.app) is False
    recovered = f.run(dict(mode='scheduled_syntax_recover', scheduled_job_id=result['job_id']))
    assert recovered['details']['scheduled_syntax']['state'] == 'unconfirmed'
    assert len(f.calls) == 1


def test_pending_new_review_prevents_idle_waiting_execution(fixture):
    f = fixture
    job_id = wait_locally(f)
    f.local_ram = 8
    f.now += 31
    row = f.prepare()
    assert scheduled.idle(f.app) is False and f.calls == []
    f.decide(row, 'reject')
    f.app._scheduled_syntax_next = 0
    assert scheduled.idle(f.app) is True
    with f.app.lock:
        current = f.app.current or next(reversed(f.app.jobs))
    assert wait_for_job(f.app, current)['state'] == 'done'
    assert f.store().get(job_id)['state'] == 'completed'


def test_legacy_exact_remote_review_approve_and_recover_still_use_legacy_path(fixture):
    f = fixture
    row = f.prepare('remote_syntax_prepare')
    assert row['kind'] == 'remote_syntax'
    result = f.decide(row)['details']['remote_syntax']
    assert result['state'] == 'completed' and result['evidence']['status'] == 'passed'
    assert [kind for kind, _ in f.calls] == ['sign', 'surface']
    assert f.store().list() == []
    recovered = f.run(dict(mode='remote_syntax_recover', remote_job_id=result['job_id']))
    assert recovered['details']['remote_syntax']['state'] == 'completed'
    assert [kind for kind, _ in f.calls] == ['sign', 'surface']


def test_automatic_surface_selection_uses_both_durable_claims_and_exact_approval(fixture):
    f = fixture
    f.local_ram = 2
    row = f.prepare()
    assert row['review']['destination']['device_id'] == 'surface'
    assert f.calls == []
    result = f.decide(row)['details']['scheduled_syntax']
    assert result['state'] == 'completed'
    assert [kind for kind, _ in f.calls] == ['sign', 'surface']
    waiting = f.store().get(result['job_id'])
    from prometheus_assistant.remote_worker_client import OwnerLedger
    ledger = OwnerLedger(f.root, f.owner, authority_digest='f'*64)
    original = ledger.request(result['job_id'])
    assert original == waiting['request'] == f.calls[-1][1]
    assert ledger.lookup(original)['receipt'] == waiting['result']['receipt']


def test_surface_lost_completion_recovery_never_signs_or_dispatches_again(fixture):
    f = fixture
    f.local_ram = 2
    f.remote_error = OSError('Simulated reply loss')
    result = f.decide(f.prepare())['details']['scheduled_syntax']
    assert result['state'] == 'unconfirmed'
    assert [kind for kind, _ in f.calls] == ['sign', 'surface']
    f.restart()
    f.status()
    recovered = f.run(dict(mode='scheduled_syntax_recover', scheduled_job_id=result['job_id']))
    assert recovered['details']['scheduled_syntax']['state'] == 'unconfirmed'
    assert [kind for kind, _ in f.calls] == ['sign', 'surface']


@pytest.mark.parametrize('changed', ['fingerprint', 'session', 'owner', 'binding', 'locked'])
def test_changed_exact_approval_cannot_publish_or_execute(fixture, changed):
    f = fixture
    original_owner = f.owner
    row = f.prepare()
    body = dict(mode='approval_decision', approval_id=row['id'],
        approval_fingerprint=row['fingerprint'], approval_decision='approve', session=row['session'])
    if changed == 'fingerprint': body['approval_fingerprint'] = '0'*64
    if changed == 'session': body['session'] = '0'*32
    if changed == 'owner': f.owner = '0'*64
    if changed == 'binding': f.binding = '0'*64
    if changed == 'locked': f.valid = False
    result = f.submit(body)
    assert result['state'] == 'error'
    assert f.store(original_owner).list() == [] and f.calls == []


@pytest.mark.parametrize('control', ['busy', 'closing', 'stopped'])
def test_idle_does_not_start_while_app_busy_closing_or_stopped(fixture, control, monkeypatch):
    f = fixture
    wait_locally(f)
    f.local_ram = 8
    f.now += 31
    if control == 'busy': monkeypatch.setattr(f.app, 'current', 'other-job')
    if control == 'closing': monkeypatch.setattr(f.app, 'closing', True)
    if control == 'stopped': f.app.cancel_event.set()
    try:
        assert scheduled.idle(f.app) is False and f.calls == []
    finally:
        if control == 'busy': f.app.current = None
        if control == 'closing': f.app.closing = False


def test_chat_approval_routes_to_pending_scheduled_review(fixture):
    f = fixture
    row = f.prepare()
    result = f.run(dict(mode='chat', prompt='approve', session=row['session']))
    assert result['details']['scheduled_syntax']['state'] == 'completed'
    assert [kind for kind, _ in f.calls] == ['local']


@pytest.mark.parametrize('body', [
    dict(mode='scheduled_syntax_prepare'),
    dict(mode='scheduled_syntax_prepare', project='here', remote_files=[]),
    dict(mode='scheduled_syntax_prepare', project='here', remote_files='a.py\n\nb.py'),
    dict(mode='scheduled_syntax_prepare', project='here', remote_files='\n'.join(['a.py']*9)),
    dict(mode='scheduled_syntax_prepare', project='here', remote_files='a.py', scheduled_job_id='a'*32),
    dict(mode='scheduled_syntax_prepare', project='here', remote_files='a.py', remote_job_id='a'*32),
    dict(mode='scheduled_syntax_tick', remote_files='a.py'),
    dict(mode='scheduled_syntax_tick', scheduled_job_id='a'*32),
    dict(mode='scheduled_syntax_cancel', scheduled_job_id='bad'),
    dict(mode='scheduled_syntax_cancel', scheduled_job_id=True),
    dict(mode='scheduled_syntax_recover'),
    dict(mode='scheduled_syntax_recover', scheduled_job_id='A'*32),
    dict(mode='chat', prompt='hello', scheduled_job_id='a'*32),
    dict(mode='remote_syntax_info', scheduled_job_id='a'*32),
])
def test_malformed_scheduled_requests_are_rejected_by_ui_validation(body):
    with pytest.raises(ValueError):
        ui.validate_job(body)


@pytest.mark.parametrize('mode', sorted(scheduled.MODES))
def test_direct_unauthenticated_interface_execution_rejected(fixture, mode):
    body = dict(mode=mode)
    if mode == 'scheduled_syntax_prepare':
        body.update(project=str(fixture.project), remote_files='hello.py')
    if mode in {'scheduled_syntax_cancel', 'scheduled_syntax_recover'}:
        body['scheduled_job_id'] = 'a'*32
    with pytest.raises(ValueError, match='authenticated'):
        fixture.app.execute(body)
