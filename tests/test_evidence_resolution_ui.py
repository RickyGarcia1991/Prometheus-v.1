from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import time

import pytest
from prometheus_assistant import evidence_resolution_ui as adapter, ui_server as ui
from test_shared_capability_evidence import complete, O, B
from test_grants_http import endpoint


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app = ui.Interface(tmp_path/'data/memory.sqlite3')
    auth = {'valid': True}
    @contextmanager
    def owner():
        if not auth['valid']: raise ValueError('Owner expired')
        yield O
    monkeypatch.setattr(ui, 'reference_session', nullcontext)
    first = complete(app.action_approvals)
    complete(app.action_approvals, status='checks_failed')
    row = next(r for r in app.action_approvals.history(owner=O) if r['id']==first['id'])
    conflict = row['evidence_conflict']
    request = dict(mode='evidence_resolution_prepare', resolution_target_id=row['id'],
        resolution_target_fingerprint=row['fingerprint'], resolution_snapshot_sha256=conflict['snapshot_sha256'],
        resolution_fact_sha256=conflict['variants'][0]['fact_sha256'], resolution_reason='Exact native observation; retain the contradiction.')
    def run(body): return adapter.execute(app, ui.validate_job(body), grant_owner=owner, binding=B)
    yield SimpleNamespace(app=app, owner=owner, run=run, auth=auth, request=request, original=row)
    app.close()


def decision(row, value='approve'):
    return dict(mode='approval_decision', session=row['session'], approval_id=row['id'],
        approval_fingerprint=row['fingerprint'], approval_decision=value)


def test_review_and_decision_are_bound_and_preserve_originals(fixture):
    f=fixture
    result=f.run(f.request); row=result['details']['approval']
    assert row['kind']=='evidence_resolution'
    assert row['review']['reason']==f.request['resolution_reason']
    assert row['review']['fact_sha256']==f.request['resolution_fact_sha256']
    assert row['review']['snapshot_sha256']==f.request['resolution_snapshot_sha256']
    result=f.run(decision(row))
    assert result['details']['evidence_resolution']['decision']=='approve'
    assert 'No work was executed' in result['reply']
    original=next(r for r in f.app.action_approvals.history(owner=O) if r['id']==f.original['id'])
    assert original['evidence_conflict']==f.original['evidence_conflict']
    assert original['capability_outcomes']==f.original['capability_outcomes']
    assert f.run(decision(row)) is None


@pytest.mark.parametrize('failure',['owner','fingerprint','session','policy','cancel','request_changes'])
def test_invalid_live_decision_never_records_interpretation(fixture, monkeypatch, failure):
    f=fixture; row=f.run(f.request)['details']['approval']; body=decision(row)
    if failure=='owner': f.auth['valid']=False
    if failure=='fingerprint': body['approval_fingerprint']='e'*64
    if failure=='session': body['session']='e'*32
    if failure=='policy': monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','ui.evidence_resolution_prepare')
    if failure=='cancel': f.app.cancel_event.set()
    if failure=='request_changes': body.update(approval_decision='request_changes',approval_feedback='Change the selected observation')
    with pytest.raises((ValueError,RuntimeError)): f.run(body)
    assert f.app.action_approvals.pending(owner=O,binding=B)['id']==row['id']


def test_routed_authenticated_path_and_direct_rejection(fixture):
    f=fixture
    with pytest.raises(ValueError,match='authenticated'): f.app.execute(f.request)
    def submit(body):
        job=f.app.submit(body,grant_owner=f.owner,approval_binding=B)
        end=time.monotonic()+5
        while time.monotonic()<end:
            with f.app.lock: result=dict(f.app.jobs[job['id']])
            if result['state']!='running': return result
            time.sleep(.01)
        pytest.fail('Resolution route did not finish')
    prepared=submit(f.request); assert prepared['state']=='done',prepared
    decided=submit(decision(prepared['result']['details']['approval'],'reject'))
    assert decided['state']=='done',decided
    assert decided['result']['details']['evidence_resolution']['decision']=='reject'


@pytest.mark.parametrize('change',[{'resolution_fact_sha256':'bad'},{'resolution_reason':''},
    {'resolution_reason':'x'*2001},{'resolution_reason':'x\x00'}, {'resolution_reason':None},
    {'prompt':42},{'prompt':'embedded instruction'}, {'project':'C:/other'}, {'command':'shell'},
    {'resolution_snapshot_sha256':True},{'resolution_target_id':'d'*64}])
def test_invalid_payloads_are_rejected(fixture,change):
    with pytest.raises(ValueError): ui.validate_job(fixture.request|change)


def test_resolution_fields_cannot_be_smuggled_into_other_modes():
    with pytest.raises(ValueError): ui.validate_job({'mode':'chat','prompt':'hello','resolution_reason':'test'})


def test_http_review_requires_owner_origin_and_token_then_survives_history_reload(endpoint,monkeypatch):
    app,gate,effects,request=endpoint
    monkeypatch.setattr(ui,'reference_session',nullcontext)
    with gate.grant_owner('fixture-cookie') as owner:
        first=complete(app.action_approvals,owner=owner)
        complete(app.action_approvals,owner=owner,status='checks_failed')
    code,history=request('/api/decisions/history');assert code==200
    row=next(r for r in history['items'] if r['id']==first['id']);conflict=row['evidence_conflict']
    body=dict(mode='evidence_resolution_prepare',resolution_target_id=row['id'],
        resolution_target_fingerprint=row['fingerprint'],resolution_snapshot_sha256=conflict['snapshot_sha256'],
        resolution_fact_sha256=conflict['variants'][0]['fact_sha256'],resolution_reason='HTTP fixture interpretation')
    for kwargs,expected in [({'owner':False},401),({'token':False},403),({'origin':'https://foreign.example'},403)]:
        assert request('/api/jobs',body,**kwargs)[0]==expected
    def submit(body):
        code,job=request('/api/jobs',body);assert code==202,job
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            code,result=request('/api/job/'+job['id']);assert code==200
            if result['state']!='running':break
            time.sleep(.01)
        assert result['state']=='done',result
        return result['result']
    pending=submit(body)['details']['approval']
    submit(decision(pending))
    code,history=request('/api/decisions/history');assert code==200
    original=next(r for r in history['items'] if r['id']==first['id'])
    assert original['evidence_conflict']==conflict
    assert original['evidence_resolutions']['items'][0]['recording_state']=='recorded'
    assert not effects


def test_resolution_script_is_served_by_actual_http_server(tmp_path):
    import http.client
    import threading
    app=ui.Interface(tmp_path/'data/memory.sqlite3')
    server=ui.Server(('127.0.0.1',0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    try:
        connection.request('GET','/evidence-resolution.js',headers={'Host':server.owner_origin.removeprefix('http://')})
        response=connection.getresponse()
        assert response.status==200
        assert response.getheader('Content-Type').startswith('text/javascript')
        assert response.read()==(ui.ASSETS/'evidence-resolution.js').read_bytes()
    finally:
        connection.close();server.shutdown();server.server_close();thread.join(3);app.close()
