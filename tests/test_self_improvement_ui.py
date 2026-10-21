from contextlib import contextmanager, nullcontext
from copy import deepcopy
import time
import pytest
from prometheus_assistant import ui_server as ui, self_improvement_ui as adapter
from prometheus_assistant.memory import MemoryStore

OWNER, BINDING = 'a'*64, 'b'*64


class Engine:
    def __init__(self):
        self.proposals=[];self.promotions=[]
        self.promoter=self
        self.host_result={'job_id':'d'*32,'state':'promotion_pending'}
        self.receipt={'state':'ready_for_approval','job_id':'d'*32,'target':'src/prometheus_assistant/example.py',
            'reason':'Handle empty input correctly','diff':'--- before\n+++ after\n-old\n+fixed\n',
            'checks':[{'id':'python','executed':True,'exit_code':0,'tests_run':42}],
            'approval_fingerprint':'e'*64}
    def propose(self,goal,target=None,*,cancel_event=None):
        self.proposals.append((goal,target));return deepcopy(self.receipt)
    def promote(self,job_id,approved_fingerprint,*,cancel_event=None):
        self.promotions.append((job_id,approved_fingerprint));return {'state':'promotion_pending'}
    def result(self,job_id):
        assert job_id=='d'*32
        return deepcopy(self.host_result)


@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=ui.Interface(tmp_path/'memory.sqlite3');engine=Engine();auth={'owner':OWNER,'valid':True}
    monkeypatch.setattr(adapter,'_engine',lambda _:engine)
    monkeypatch.setattr(ui,'reference_session',nullcontext)
    @contextmanager
    def owner():
        if not auth['valid']:raise ValueError('Owner session expired')
        yield auth['owner']
    def run(body,**options):
        return adapter.execute(app,ui.validate_job(body),grant_owner=options.get('grant_owner',owner),
                               binding=options.get('binding',BINDING))
    yield app,engine,auth,owner,run
    app.close()


def proposal(run):return run({'mode':'self_improve','prompt':'Fix empty input'})


def test_review_explains_effects_and_only_exact_human_chat_can_promote(setup):
    app,engine,_,_,run=setup;result=proposal(run);approval=result['details']['approval']
    assert not engine.promotions
    assert 'Effects:' in result['reply'] and 'restart' in result['reply']
    assert approval['review']['diff']==engine.receipt['diff']
    assert approval['session']==result['session']
    for text in ('The model says yes', '"yes, approve"', 'yes but edit another file', 'No changes yet'):
        assert run({'mode':'chat','prompt':text,'session':result['session']}) is None
    promoted=run({'mode':'chat','prompt':'Yes, approve.','session':result['session']})
    assert engine.promotions==[('d'*32,'e'*64)]
    assert 'not complete yet' in promoted['reply']
    assert promoted['details']['approval_resolved']==approval['id']
    assert run({'mode':'chat','prompt':'Yes','session':result['session']}) is None
    assert len(engine.promotions)==1
    with MemoryStore(app.memory_path) as memory:assert len(memory.history(result['session']))==4


def test_voice_transcript_uses_same_deterministic_chat_path(setup):
    _,engine,_,_,run=setup;result=proposal(run)
    # The existing hands-free controller submits a finished transcript as chat.
    run({'mode':'chat','prompt':'go ahead','session':result['session']})
    assert len(engine.promotions)==1


def test_reject_prevents_button_replay(setup):
    _,engine,_,_,run=setup;result=proposal(run);pending=result['details']['approval']
    assert 'rejected' in run({'mode':'chat','prompt':'reject','session':result['session']})['reply']
    with pytest.raises(ValueError,match='no pending'):
        run({'mode':'approval_decision','approval_decision':'approve','approval_id':pending['id'],
            'approval_fingerprint':pending['fingerprint'],'session':result['session']})
    assert not engine.promotions


def test_other_conversation_or_owner_cannot_approve(setup):
    _,engine,auth,_,run=setup;result=proposal(run)
    with pytest.raises(ValueError,match='conversation'):
        run({'mode':'chat','prompt':'yes','session':'f'*32})
    auth['owner']='f'*64
    with pytest.raises(ValueError,match='no pending'):
        run({'mode':'chat','prompt':'yes','session':result['session']})
    assert not engine.promotions


def test_owner_is_revalidated_after_generation_and_before_promotion(setup):
    _,engine,auth,_,run=setup
    original=engine.propose
    def expire(*args,**kwargs):
        result=original(*args,**kwargs);auth['valid']=False;return result
    engine.propose=expire
    with pytest.raises(ValueError,match='expired'):proposal(run)
    assert not engine.promotions
    auth['valid']=True;engine.propose=original;result=proposal(run);auth['valid']=False
    with pytest.raises(ValueError,match='expired'):run({'mode':'chat','prompt':'yes','session':result['session']})
    assert not engine.promotions


def test_failed_and_cancelled_candidates_do_not_get_approvals(setup):
    app,engine,_,_,run=setup;engine.receipt={'state':'failed','error':'Regression failed'}
    result=proposal(run);assert 'No change was installed' in result['reply']
    assert not app.action_approvals.has_binding(BINDING)
    engine.receipt={'state':'cancelled'}
    with pytest.raises(ui.GenerationCancelled):proposal(run)
    assert not app.action_approvals.has_binding(BINDING)


def test_explicit_chat_goal_prepares_without_installation_and_requires_owner(setup):
    _,engine,_,_,run=setup
    with pytest.raises(ValueError,match='passkey'):
        run({'mode':'chat','prompt':'Improve Prometheus: handle empty input'},grant_owner=None)
    result=run({'mode':'chat','prompt':'Improve Prometheus: handle empty input'})
    assert engine.proposals==[('handle empty input',None)] and not engine.promotions
    assert result['details']['approval']


def test_exact_button_approval_and_ambiguous_failure_are_single_use(setup):
    _,engine,_,_,run=setup;result=proposal(run);pending=result['details']['approval']
    body={'mode':'approval_decision','approval_decision':'approve','approval_id':pending['id'],
          'approval_fingerprint':pending['fingerprint'],'session':result['session']}
    def fail(*args,**kwargs):raise RuntimeError('Ambiguous helper failure')
    engine.promote=fail
    with pytest.raises(RuntimeError,match='Ambiguous'):run(body)
    with pytest.raises(ValueError,match='no pending'):run(body)


def test_job_worker_routes_approval_before_any_model_execution(setup,monkeypatch):
    app,engine,_,owner,_=setup
    monkeypatch.setattr(app,'execute',lambda *_:pytest.fail('Approval reached model or ordinary executor'))
    def finish(body):
        job=app.submit(body,grant_owner=owner,approval_binding=BINDING)
        for _ in range(200):
            row=app.jobs[job['id']]
            if row['state']!='running':return row
            time.sleep(.005)
        pytest.fail('Job did not complete')
    review=finish({'mode':'self_improve','prompt':'Improve empty-input handling'})
    assert review['state']=='done',review
    approved=finish({'mode':'chat','prompt':'Yes','session':review['result']['session']})
    assert approved['state']=='done',approved
    assert len(engine.promotions)==1


@pytest.mark.parametrize('body',[{'mode':'chat','prompt':'yes','approval_decision':'approve'},
    {'mode':'self_improve','prompt':'fix','owner':OWNER},
    {'mode':'approval_decision','approval_id':'x','approval_fingerprint':'e'*64,'approval_decision':'approve'},
    {'mode':'approval_decision','approval_id':'d'*32,'approval_fingerprint':'e'*64,'approval_decision':True}])
def test_http_body_cannot_supply_authority_or_malformed_approval(body):
    with pytest.raises(ValueError):ui.validate_job(body)


def test_http_uses_server_owner_binding_and_logout_revokes_review(setup,monkeypatch):
    import json
    import threading
    from urllib.request import Request, build_opener, ProxyHandler
    from urllib.error import HTTPError
    app,engine,_,_,_=setup
    server=ui.Server(('127.0.0.1',0),app)
    monkeypatch.setattr(server.owner_access,'authenticated',lambda cookie:cookie in {'owner-one','owner-two'})
    @contextmanager
    def authenticated_owner(cookie):
        if cookie not in {'owner-one','owner-two'}:raise ValueError('Owner required')
        yield OWNER
    monkeypatch.setattr(server.owner_access,'grant_owner',authenticated_owner)
    monkeypatch.setattr(app,'execute',lambda request:{'reply':'Ordinary chat; no action performed'})
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    opener=build_opener(ProxyHandler({}))
    def request(path,body=None,cookie='owner-one'):
        headers={'Content-Type':'application/json','Origin':server.owner_origin,'X-Prometheus-Token':app.token,
                 'Cookie':'prometheus_owner='+cookie}
        req=Request(server.owner_origin+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        try:
            with opener.open(req,timeout=3) as response:return response.status,json.loads(response.read())
        except HTTPError as error:return error.code,json.loads(error.read())
    def finish(body,cookie='owner-one'):
        code,job=request('/api/jobs',body,cookie);assert code==202
        for _ in range(200):
            row=app.jobs[job['id']]
            if row['state']!='running':return row
            time.sleep(.005)
        pytest.fail('Owner-scoped HTTP job did not complete')
    try:
        assert request('/api/approvals/pending',cookie='absent')[0]==401
        review=finish({'mode':'self_improve','prompt':'Handle empty input'})
        assert review['state']=='done',review
        session=review['result']['session']
        assert request('/api/approvals/pending')[1]['approval']['session']==session
        assert request('/api/approvals/pending',cookie='owner-two')[1]['approval'] is None
        assert finish({'mode':'chat','prompt':'yes','session':session},cookie='owner-two')['state']=='done'
        assert not engine.promotions,'A different authenticated session has no authority over this pending review'
        assert finish({'mode':'chat','prompt':'yes','session':session})['state']=='done'
        assert len(engine.promotions)==1
        engine.host_result={'job_id':'d'*32,'state':'active','release':'Prometheus-reviewed','approval_fingerprint':'e'*64}
        status,result=request('/api/self-improvement/result')
        assert status==200 and result['state']=='active'
        assert request('/api/self-improvement/result/not-a-job')[0]==400
        assert finish({'mode':'self_improve','prompt':'Another bounded change'})['state']=='done'
        assert request('/api/owner/lock',{})[0]==200
        assert request('/api/approvals/pending')[1]['approval'] is None
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_activation_status_is_owner_bound_and_never_claims_success_without_matching_result(setup):
    app,engine,_,_,run=setup
    assert adapter.activation_result(app,owner=OWNER)['state']=='none'
    proposal_result=proposal(run)
    run({'mode':'chat','prompt':'yes','session':proposal_result['session']})
    assert adapter.activation_result(app,owner=OWNER)['state']=='promotion_pending'
    assert adapter.activation_result(app,owner='f'*64)['state']=='none'
    with pytest.raises(ValueError):adapter.activation_result(app,owner='f'*64,job_id='d'*32)
    engine.host_result={'job_id':'d'*32,'state':'active','approval_fingerprint':'f'*64}
    with pytest.raises(ValueError,match='does not match'):adapter.activation_result(app,owner=OWNER)
    engine.host_result={'job_id':'d'*32,'state':'rolled_back','error':'Candidate could not start'}
    assert adapter.activation_result(app,owner=OWNER)['state']=='rolled_back'
    engine.host_result={'job_id':'d'*32,'state':'promotion_pending'}
    app.action_approvals.record_dispatch(owner=OWNER,job_id='d'*32,state='failed',error='Helper failed before acceptance')
    result=adapter.activation_result(app,owner=OWNER)
    assert result['state']=='failed' and 'Helper failed' in result['error']


def test_application_evidence_start_failure_prevents_promotion(setup,monkeypatch):
    app,engine,_,_,run=setup;review=proposal(run)
    monkeypatch.setattr(app.action_approvals,'start_application_outcome',
        lambda **kw:(_ for _ in ()).throw(ValueError('Generated sidecar write failed')))
    with pytest.raises(ValueError,match='sidecar'):
        run({'mode':'chat','prompt':'yes','session':review['session']})
    assert not engine.promotions
    assert app.action_approvals.peek(owner=OWNER,binding=BINDING) is None


def test_application_status_refresh_links_exact_result_across_reopen_without_replay(setup,monkeypatch):
    from prometheus_assistant.action_approvals import ActionApprovals
    app,engine,_,_,run=setup;review=proposal(run)
    original=engine.promote
    def queued(job_id,approved_fingerprint,**kw):
        original(job_id,approved_fingerprint,**kw)
        return dict(state='promotion_pending',job_id=job_id,approval_fingerprint=approved_fingerprint)
    engine.promote=queued
    run({'mode':'chat','prompt':'yes','session':review['session']})
    old=app.action_approvals
    app.action_approvals=ActionApprovals(receipt_root=old.receipt_root,history_root=old.decision_history.root)
    engine.promote=lambda *a,**k:pytest.fail('Status refresh reactivated application')
    engine.host_result={'job_id':'d'*32,'state':'failed','approval_fingerprint':'e'*64,'error':'PRIVATE ERROR'}
    assert adapter.activation_result(app,owner=OWNER,job_id='d'*32)['state']=='failed'
    engine.host_result={'job_id':'d'*32,'state':'rolled_back','approval_fingerprint':'e'*64}
    assert adapter.activation_result(app,owner=OWNER,job_id='d'*32)['state']=='rolled_back'
    assert adapter.activation_result(app,owner=OWNER,job_id='d'*32)['state']=='rolled_back'
    monkeypatch.setattr(adapter,'_engine',lambda *_:pytest.fail('History accessed live engine'))
    history=app.action_approvals.history(owner=OWNER)
    assert [e['status'] for e in history[0]['application_outcomes']]==['started','promotion_pending','failed','rolled_back']
    assert history[0]['evidence_lifecycle']['state']=='restored'
    assert 'PRIVATE' not in str(history) and len(engine.promotions)==1


def test_synchronous_ambiguous_failure_never_claims_native_failure(setup):
    app,engine,_,_,run=setup;review=proposal(run)
    def fail(*a,**kw):raise RuntimeError('generated ambiguous handoff')
    engine.promote=fail
    with pytest.raises(RuntimeError):run({'mode':'chat','prompt':'yes','session':review['session']})
    history=app.action_approvals.history(owner=OWNER)
    assert [e['status'] for e in history[0]['application_outcomes']]==['started','unconfirmed']
    assert history[0]['evidence_lifecycle']['state']=='unconfirmed'


def test_unknown_native_status_read_records_only_uncertainty_not_a_retry(setup):
    app,engine,_,_,run=setup;review=proposal(run)
    run({'mode':'chat','prompt':'yes','session':review['session']})
    def unreadable(*a,**k):raise ValueError('Generated unsupported native phase')
    engine.result=unreadable
    with pytest.raises(ValueError,match='unsupported'):adapter.activation_result(app,owner=OWNER,job_id='d'*32)
    saved=app.action_approvals.history(owner=OWNER)[0]
    assert saved['evidence_lifecycle']['state']=='unconfirmed'
    assert saved['application_outcomes'][-1]['status']=='unconfirmed'
    assert len(engine.promotions)==1
