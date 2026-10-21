"""Live owner lifecycle controls do not execute native work."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import time
import pytest
from prometheus_assistant import interpretation_lifecycle_ui as adapter, ui_server as ui
from test_evidence_resolution import family, prepare, decide, O, B
from test_grants_http import endpoint


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    app=ui.Interface(tmp_path/'data/memory.sqlite3');auth={'valid':True}
    @contextmanager
    def owner():
        if not auth['valid']:raise ValueError('Owner expired')
        yield O
    monkeypatch.setattr(ui,'reference_session',nullcontext)
    original=family(app.action_approvals,'worker')
    pending=prepare(app.action_approvals,original);decide(app.action_approvals,pending)
    def state():
        return next(r for r in app.action_approvals.history(owner=O) if r['id']==pending['id'])['interpretation_lifecycle']['items'][0]
    def request(operation='revise'):
        item=state();conflict=next(r for r in app.action_approvals.history(owner=O) if r['id']==original['id'])['evidence_conflict']
        value=dict(mode='interpretation_change_prepare',interpretation_id=pending['id'],
            interpretation_fingerprint=pending['fingerprint'],interpretation_state_sha256=item['state_sha256'],
            interpretation_operation=operation,interpretation_reason='Reconsidered using exact native observations.')
        if operation=='revise':value.update(interpretation_snapshot_sha256=conflict['snapshot_sha256'],interpretation_fact_sha256=conflict['variants'][-1]['fact_sha256'])
        return value
    yield SimpleNamespace(app=app,owner=owner,auth=auth,state=state,request=request,
        run=lambda body:adapter.execute(app,ui.validate_job(body),grant_owner=owner,binding=B),original=original)
    app.close()


def decision(row,value='approve'):
    return dict(mode='approval_decision',session=row['session'],approval_id=row['id'],
        approval_fingerprint=row['fingerprint'],approval_decision=value)


@pytest.mark.parametrize('operation',['revise','withdraw'])
@pytest.mark.parametrize('choice',['approve','reject'])
def test_exact_lifecycle_review_preserves_native_work(lifecycle,operation,choice):
    f=lifecycle;body=f.request(operation);row=f.run(body)['details']['approval']
    assert row['kind']=='evidence_interpretation_change'
    assert row['payload']['operation']==operation
    assert row['payload']['expected_state_sha256']==body['interpretation_state_sha256']
    result=f.run(decision(row,choice));item=result['details']['interpretation_change']
    assert item['changes'][0]['decision']==choice
    assert item['state']==('withdrawn' if choice=='approve' and operation=='withdraw' else 'active')
    original=next(r for r in f.app.action_approvals.history(owner=O) if r['id']==f.original['id'])
    for key in ('capability_outcomes','evidence_conflict','evidence_lifecycle'):
        assert original[key]==f.original[key]
    assert 'No work was executed' in result['reply']


@pytest.mark.parametrize('change',[{'interpretation_id':'bad'},{'interpretation_fingerprint':True},
    {'interpretation_reason':''},{'interpretation_reason':'x'*2001},{'interpretation_reason':'x\x00'},
    {'interpretation_reason':'x\x1b'},{'interpretation_operation':'run'}, {'interpretation_state_sha256':'x'},
    {'prompt':'run'}, {'project':'C:/other'},{'command':'shell'},{'interpretation_fact_sha256':None}])
def test_invalid_lifecycle_fields_fail_before_prepare(lifecycle,change):
    with pytest.raises(ValueError):ui.validate_job(lifecycle.request()|change)
    assert lifecycle.app.action_approvals.pending(owner=O,binding=B) is None


def test_withdrawal_forbids_replacement_and_other_modes_forbid_fields(lifecycle):
    body=lifecycle.request('withdraw')
    with pytest.raises(ValueError):ui.validate_job(body|{'interpretation_fact_sha256':'a'*64})
    with pytest.raises(ValueError):ui.validate_job({'mode':'chat','prompt':'hi','interpretation_id':'a'*32})


@pytest.mark.parametrize('failure',['owner','policy','cancel','fingerprint','session','request_changes'])
def test_live_decision_invalidated_before_effect(lifecycle,monkeypatch,failure):
    f=lifecycle;row=f.run(f.request())['details']['approval'];body=decision(row)
    if failure=='owner':f.auth['valid']=False
    if failure=='policy':monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','ui.interpretation_change_prepare')
    if failure=='cancel':f.app.cancel_event.set()
    if failure=='fingerprint':body['approval_fingerprint']='e'*64
    if failure=='session':body['session']='e'*32
    if failure=='request_changes':body.update(approval_decision='request_changes',approval_feedback='Change')
    with pytest.raises((ValueError,RuntimeError)):f.run(body)
    assert f.state()['total_changes']==0


def test_actual_job_route_requires_owner_and_completes(lifecycle):
    f=lifecycle
    with pytest.raises(ValueError,match='authenticated'):f.app.execute(f.request())
    def submit(body):
        job=f.app.submit(body,grant_owner=f.owner,approval_binding=B);deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with f.app.lock:result=dict(f.app.jobs[job['id']])
            if result['state']!='running':break
            time.sleep(.01)
        assert result['state']=='done',result
        return result['result']
    row=submit(f.request('withdraw'))['details']['approval']
    assert submit(decision(row))['details']['interpretation_change']['state']=='withdrawn'


def test_http_owner_origin_token_and_history_link(endpoint,monkeypatch):
    from prometheus_assistant.application_policy import revision
    from test_evidence_resolution import family,prepare,decide,O as fixture_owner
    app,gate,effects,request=endpoint
    monkeypatch.setattr(ui,'reference_session',nullcontext)
    with gate.grant_owner('fixture-cookie') as owner:
        # The fixture helper builds exact native observations for its fixed test owner.
        # Use that history under an isolated fixture grant; production auth is untouched.
        from test_shared_capability_evidence import complete
        first=complete(app.action_approvals,owner=owner);complete(app.action_approvals,owner=owner,status='checks_failed')
        target=next(r for r in app.action_approvals.history(owner=owner) if r['id']==first['id']);c=target['evidence_conflict']
        base=app.action_approvals.prepare_evidence_resolution(owner=owner,binding=B,session='1'*32,target_id=first['id'],target_fingerprint=first['fingerprint'],snapshot_sha256=c['snapshot_sha256'],fact_sha256=c['variants'][0]['fact_sha256'],reason='LABELED HTTP FIXTURE',policy_revision=revision())
        app.action_approvals.decide_evidence_resolution(owner=owner,binding=B,session=base['session'],approval_id=base['id'],fingerprint=base['fingerprint'],decision='approve',policy_revision=revision())
    code,h=request('/api/decisions/history');assert code==200
    item=next(r for r in h['items'] if r['id']==base['id'])['interpretation_lifecycle']['items'][0]
    body=dict(mode='interpretation_change_prepare',interpretation_id=base['id'],interpretation_fingerprint=base['fingerprint'],interpretation_state_sha256=item['state_sha256'],interpretation_operation='withdraw',interpretation_reason='Withdraw generated interpretation; keep evidence.')
    for kwargs,expected in [({'owner':False},401),({'token':False},403),({'origin':'https://foreign.example'},403)]:
        assert request('/api/jobs',body,**kwargs)[0]==expected
    def submit(value):
        code,job=request('/api/jobs',value);assert code==202,job
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            code,result=request('/api/job/'+job['id']);assert code==200
            if result['state']!='running':break
            time.sleep(.01)
        assert result['state']=='done',result
        return result['result']
    row=submit(body)['details']['approval'];submit(decision(row))
    code,h=request('/api/decisions/history');assert code==200
    original=next(r for r in h['items'] if r['id']==first['id'])
    assert original['evidence_conflict']==c
    assert original['interpretation_lifecycle']['items'][0]['state']=='withdrawn'
    assert not effects


def test_lifecycle_asset_served_by_actual_server(tmp_path):
    import http.client,threading
    app=ui.Interface(tmp_path/'data/memory.sqlite3');server=ui.Server(('127.0.0.1',0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    try:
        conn.request('GET','/interpretation-lifecycle.js',headers={'Host':server.owner_origin.removeprefix('http://')})
        response=conn.getresponse();assert response.status==200
        assert response.getheader('Content-Type').startswith('text/javascript')
        assert response.read()==(ui.ASSETS/'interpretation-lifecycle.js').read_bytes()
    finally:
        conn.close();server.shutdown();server.server_close();thread.join(3);app.close()
