from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import time
import pytest
from prometheus_assistant import remote_jobs_ui as jobs, ui_server as ui


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app=ui.Interface(tmp_path/'data/memory.sqlite3')
    project=tmp_path/'project';project.mkdir();(project/'hello.py').write_bytes(b'answer=42\n')
    f=SimpleNamespace(app=app,project=project,calls=[],valid=True,owner='a'*64)
    @contextmanager
    def owner():
        if not f.valid: raise ValueError('expired')
        yield f.owner
    f.owner_grant=owner
    target=dict(account_id='account',device_id='surface',device_name='Surface',worker_digest='b'*64,online=True)
    def dispatch(envelope):
        f.calls.append('dispatch'); req=envelope['payload']['request']
        return {**{k:req[k] for k in ('schema_version','job_id','device_id','worker_digest','request_digest')},
                'state':'completed','checks':[{'path':row['path'],'passed':True} for row in req['files']]}
    def sign(_): f.calls.append('sign');return b'x'*256
    app._remote_worker_client=SimpleNamespace(root=tmp_path/'host',target=lambda:dict(target),signer=sign,
        dispatch=dispatch,clock=lambda:100,recover=lambda req,ledger:ledger.lookup(req),
        admission=lambda req=None:dict(decision='ready',reasons=[],authenticated=True,observed_at=100,expires_at=130))
    f.run=lambda body:jobs.execute(app,ui.validate_job(body),grant_owner=owner,binding='c'*64)
    f.prepare=lambda:f.run(dict(mode='remote_syntax_prepare',project=str(project),remote_files='hello.py'))
    def decide(row,choice='approve'):
        return f.run(dict(mode='approval_decision',approval_id=row['id'],approval_fingerprint=row['fingerprint'],
                          approval_decision=choice,session=row['session']))
    f.decide=decide
    monkeypatch.setattr(ui,'reference_session',nullcontext)
    yield f
    app.close()


def test_exact_file_review_deny_and_success(fixture):
    f=fixture; row=f.prepare()['details']['approval']
    assert row['review']['files'][0]['content']=='answer=42\n'
    assert f.calls==[]
    assert f.decide(row,'reject')['details']['remote_syntax']['state']=='rejected'
    assert not (f.app._remote_worker_client.root/'owners'/f.owner/'jobs.sqlite3').exists()
    row=f.prepare()['details']['approval']; result=f.decide(row)['details']['remote_syntax']
    assert result['state']=='completed' and result['evidence']['status']=='passed'
    assert f.calls==['sign','dispatch']
    recovered=f.run(dict(mode='remote_syntax_recover',remote_job_id=result['job_id']))
    assert recovered['details']['remote_syntax']['state']=='completed' and f.calls==['sign','dispatch']


def test_snapshot_not_changed_by_later_file_edit(fixture):
    f=fixture; row=f.prepare()['details']['approval']; (f.project/'hello.py').write_text('wrong =\n')
    assert row['payload']['request']['files'][0]['content']=='answer=42\n'
    assert f.decide(row)['details']['remote_syntax']['state']=='completed'


@pytest.mark.parametrize('mutation',['owner','fingerprint','session','cancel'])
def test_invalid_review_cannot_dispatch(fixture,mutation):
    f=fixture; row=f.prepare()['details']['approval']
    if mutation=='owner':f.valid=False
    if mutation=='fingerprint':row['fingerprint']='d'*64
    if mutation=='session':row['session']='d'*32
    if mutation=='cancel':f.app.cancel_event.set()
    with pytest.raises(ValueError):f.decide(row)
    assert f.calls==[]


def test_owner_cannot_recover_another_owners_job(fixture):
    f=fixture; result=f.decide(f.prepare()['details']['approval'])['details']['remote_syntax']
    f.owner='e'*64
    with pytest.raises(ValueError):f.run(dict(mode='remote_syntax_recover',remote_job_id=result['job_id']))


def test_missing_worker_is_visible_without_enrollment(fixture):
    f=fixture
    def missing():raise ValueError('Worker is not installed')
    f.app._remote_worker_client.target=missing
    assert f.run(dict(mode='remote_syntax_info'))['details']['remote_worker']=={'ready':False,'reason':'Worker is not installed'}
    assert f.calls==[]


@pytest.mark.parametrize('body',[
    {'mode':'remote_syntax_prepare'}, {'mode':'remote_syntax_recover','remote_job_id':'bad'},
    {'mode':'chat','prompt':'hello','remote_files':'a.py'}, {'mode':'remote_syntax_info','remote_files':'a.py'},
    {'mode':'remote_syntax_prepare','project':'here','remote_files':'a.py','remote_job_id':'a'*32}])
def test_invalid_fields(body):
    with pytest.raises(ValueError):ui.validate_job(body)


def test_real_authenticated_submit_path(fixture):
    f=fixture
    started=f.app.submit(dict(mode='remote_syntax_prepare',project=str(f.project),remote_files='hello.py'),
                         grant_owner=f.owner_grant,approval_binding='c'*64)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with f.app.lock:result=dict(f.app.jobs[started['id']])
        if result['state']!='running':break
        time.sleep(.01)
    assert result['state']=='done',result
    assert result['result']['details']['approval']['kind']=='remote_syntax'
    assert f.calls==[]


def test_direct_unauthed_execute_rejected(fixture):
    with pytest.raises(ValueError,match='authenticated'):
        fixture.app.execute(dict(mode='remote_syntax_info'))


@pytest.mark.parametrize('action',['cancel','lock'])
def test_owner_control_remains_available_during_resource_query(fixture,action):
    import threading
    f=fixture;row=f.prepare()['details']['approval']
    owner_lock=threading.RLock();entered=threading.Event();release=threading.Event();controlled=threading.Event()
    outcome={}
    @contextmanager
    def owner():
        with owner_lock:
            if not f.valid:raise ValueError('Owner locked')
            yield f.owner
    def admission(req):
        entered.set()
        assert release.wait(4)
        return dict(decision='ready',reasons=[],authenticated=True,observed_at=100,expires_at=130)
    f.app._remote_worker_client.admission=admission
    def approve():
        try:
            outcome['value']=jobs.execute(f.app,ui.validate_job(dict(mode='approval_decision',approval_id=row['id'],
                approval_fingerprint=row['fingerprint'],approval_decision='approve',session=row['session'])),
                grant_owner=owner,binding='c'*64)
        except Exception as error:outcome['error']=error
    def control():
        with owner_lock:
            if action=='cancel':f.app.cancel_event.set()
            else:f.valid=False
            controlled.set()
    worker=threading.Thread(target=approve);worker.start()
    try:
        assert entered.wait(2)
        command=threading.Thread(target=control);command.start()
        assert controlled.wait(2),'Stop/Lock was blocked by the resource query'
    finally:
        release.set();worker.join(5)
        if 'command' in locals():command.join(5)
    assert not worker.is_alive()
    assert f.calls==[] and not (f.app._remote_worker_client.root/'owners'/f.owner/'jobs.sqlite3').exists()
    if action=='cancel':assert outcome['value']['details']['remote_syntax']['state']=='cancelled'
    else:assert isinstance(outcome.get('error'),ValueError)
