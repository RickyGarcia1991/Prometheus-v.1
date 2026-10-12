from concurrent.futures import ThreadPoolExecutor
import secrets
import threading
import pytest
from prometheus_assistant.scoped_grants import GrantStore, scope
from prometheus_assistant.tool_registry import ToolSpec, ArgSpec


def fixture(tmp_path, executor=lambda r: 'ran'):
    clock = [1000.0, 50.0]
    binding = ['host-a', 'revision-a']
    store = GrantStore(tmp_path/'grants.db', host=lambda: binding[0], policy_revision=lambda: binding[1],
                       wall=lambda: clock[0], monotonic=lambda: clock[1])
    spec = ToolSpec('project', 'tests', 'bounded tests', executor, {'target': ArgSpec(required=False)}, command_execution=True)
    return store, spec, clock, binding


def issue(store, spec, *, uses=2, seconds=60):
    review = store.review(spec, {'target':'tests'}, owner='owner-a', uses=uses, seconds=seconds)
    return store.issue(review['review_id'], review['scope_sha256'], owner='owner-a')


def invoke(store, spec, grant, **kwargs):
    return store.run(grant['id'], kwargs.pop('invocation', secrets.token_hex(16)), spec,
                     kwargs.pop('arguments', {'target':'tests'}), owner=kwargs.pop('owner','owner-a'))


def test_explicit_review_required_single_use_and_durable_no_arguments(tmp_path):
    store, spec, _, _ = fixture(tmp_path)
    review = store.review(spec, {'target':'tests'}, owner='owner-a', uses=1, seconds=10)
    assert not store.path.exists()
    grant = store.issue(review['review_id'], review['scope_sha256'], owner='owner-a')
    with pytest.raises(ValueError): store.issue(review['review_id'], review['scope_sha256'], owner='owner-a')
    assert invoke(store, spec, grant) == 'ran'
    with pytest.raises(ValueError): invoke(store, spec, grant)
    assert store.inspect(owner='owner-a')[0]['state'] == 'exhausted'


@pytest.mark.parametrize('field,value', [('seconds',0),('seconds',901),('seconds',True),('uses',0),('uses',6),('uses',True)])
def test_bounds(tmp_path, field, value):
    store,spec,_,_=fixture(tmp_path)
    fields={'seconds':60,'uses':1,field:value}
    with pytest.raises(ValueError):store.review(spec,{'target':'tests'},owner='owner-a',**fields)


@pytest.mark.parametrize('arguments',[{'target':'*'},{'target':'tests/*'},{'target':'../tests'},{'target':'tests','approved':True}])
def test_no_wildcards_or_scope_spoofing(tmp_path,arguments):
    _,spec,_,_=fixture(tmp_path)
    with pytest.raises(ValueError):scope(spec,arguments)


def test_mismatch_cannot_spend_or_widen_and_replay_cannot_execute(tmp_path):
    calls=[]
    store,spec,_,_=fixture(tmp_path,lambda r:calls.append(r.arguments['target']))
    grant=issue(store,spec)
    with pytest.raises(ValueError):invoke(store,spec,grant,arguments={'target':'other'})
    assert store.inspect(owner='owner-a')[0]['remaining']==2
    invocation=secrets.token_hex(16)
    invoke(store,spec,grant,invocation=invocation)
    with pytest.raises(ValueError):invoke(store,spec,grant,invocation=invocation)
    assert calls==['tests']
    assert store.inspect(owner='owner-a')[0]['remaining']==1


@pytest.mark.parametrize('change',['host','owner','revision','wall_expiry','mono_expiry','rollback','revoke'])
def test_binding_expiry_revocation_and_restart_fail_closed(tmp_path,change):
    calls=[]
    store,spec,clock,binding=fixture(tmp_path,lambda r:calls.append(1))
    grant=issue(store,spec)
    owner='owner-a'
    if change=='host':binding[0]='host-b'
    if change=='owner':owner='owner-b'
    if change=='revision':binding[1]='revision-b'
    if change=='wall_expiry':clock[0]+=61
    if change=='mono_expiry':clock[1]+=61
    if change=='rollback':clock[0]-=1
    if change=='revoke':store.revoke(grant['id'],owner=owner)
    with pytest.raises(ValueError):invoke(store,spec,grant,owner=owner)
    assert calls==[]
    # Recovery of time must not resurrect a clock-invalid record.
    if change=='rollback':
        clock[0]=1001
        with pytest.raises(ValueError):invoke(store,spec,grant)


def test_grant_survives_ordinary_restart_with_budget_and_replay_state(tmp_path):
    store,spec,clock,binding=fixture(tmp_path)
    grant=issue(store,spec)
    invocation=secrets.token_hex(16)
    assert invoke(store,spec,grant,invocation=invocation)=='ran'
    restarted=GrantStore(store.path,host=lambda:binding[0],policy_revision=lambda:binding[1],
                         wall=lambda:clock[0],monotonic=lambda:clock[1])
    with pytest.raises(ValueError):invoke(restarted,spec,grant,invocation=invocation)
    assert invoke(restarted,spec,grant)=='ran'
    with pytest.raises(ValueError):invoke(store,spec,grant)


@pytest.mark.parametrize('restart,restore',[(False,'db'),(True,'db'),(False,'witness'),(True,'witness'),(False,'both_running')])
def test_older_authority_state_does_not_resurrect_uses(tmp_path,restart,restore):
    store,spec,clock,binding=fixture(tmp_path)
    grant=issue(store,spec,uses=1)
    old_db=store.path.read_bytes();old_witness=store.ledger.witness.read_bytes()
    invoke(store,spec,grant)
    if restore in {'db','both_running'}:store.path.write_bytes(old_db)
    if restore in {'witness','both_running'}:store.ledger.witness.write_bytes(old_witness)
    if restart:store=GrantStore(store.path,host=lambda:binding[0],policy_revision=lambda:binding[1],wall=lambda:clock[0],monotonic=lambda:clock[1])
    with pytest.raises(ValueError,match='continuity|authority|Authority'):invoke(store,spec,grant)


def test_revocation_persists_across_restart(tmp_path):
    store,spec,clock,binding=fixture(tmp_path)
    grant=issue(store,spec);store.revoke(grant['id'],owner='owner-a')
    restarted=GrantStore(store.path,host=lambda:binding[0],policy_revision=lambda:binding[1],wall=lambda:clock[0],monotonic=lambda:clock[1])
    assert restarted.inspect(owner='owner-a')[0]['state']=='revoked'
    with pytest.raises(ValueError):invoke(restarted,spec,grant)


def test_other_owner_inspection_and_wrong_owner_use_preserve_original(tmp_path):
    store,spec,_,_=fixture(tmp_path)
    grant=issue(store,spec)
    assert store.inspect(owner='owner-b')==[]
    with pytest.raises(ValueError):invoke(store,spec,grant,owner='owner-b')
    assert store.inspect(owner='owner-a')[0]['remaining']==2
    assert store.inspect(owner='owner-a')[0]['state']=='active'
    assert invoke(store,spec,grant)=='ran'


def test_reserved_final_attempt_survives_interleaved_inspection(tmp_path,monkeypatch):
    from contextlib import contextmanager
    store,spec,clock,binding=fixture(tmp_path)
    grant=issue(store,spec,uses=1)
    other=GrantStore(store.path,host=lambda:binding[0],policy_revision=lambda:binding[1],wall=lambda:clock[0],monotonic=lambda:clock[1])
    original=store._db;count=[0]
    @contextmanager
    def barrier():
        count[0]+=1
        if count[0]==2:
            assert other.inspect(owner='owner-a')[0]['state']=='exhausted'
        with original() as db:yield db
    monkeypatch.setattr(store,'_db',barrier)
    assert invoke(store,spec,grant)=='ran'
    with pytest.raises(ValueError):invoke(other,spec,grant)


def test_reboot_clock_reset_retires_grants_but_allows_new_owner_review(tmp_path):
    store,spec,clock,binding=fixture(tmp_path)
    old=issue(store,spec)
    clock[0]+=10;clock[1]=1
    restarted=GrantStore(store.path,host=lambda:binding[0],policy_revision=lambda:binding[1],wall=lambda:clock[0],monotonic=lambda:clock[1])
    assert restarted.inspect(owner='owner-a')[0]['state']=='clock_invalid'
    with pytest.raises(ValueError):invoke(restarted,spec,old)
    new=issue(restarted,spec)
    assert invoke(restarted,spec,new)=='ran'


def test_same_source_on_different_project_root_cannot_reuse_grant(tmp_path,monkeypatch):
    from prometheus_assistant import builtin_tools
    root=tmp_path/'first';root.mkdir();(root/'tests').mkdir()
    other=tmp_path/'second';other.mkdir();(other/'tests').mkdir()
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:root)
    store,spec,_,_=fixture(tmp_path)
    grant=issue(store,spec)
    assert str(root/'tests')==grant['target']
    monkeypatch.setattr(builtin_tools,'_project_root',lambda:other)
    with pytest.raises(ValueError):invoke(store,spec,grant)


def _process_consume(path,grant,ready,start,results,effect_path):
    from pathlib import Path
    store=GrantStore(path,host=lambda:'host-a',policy_revision=lambda:'revision-a',wall=lambda:1000.0,monotonic=lambda:50.0)
    def effect(request):
        with Path(effect_path).open('a',encoding='utf-8') as stream:stream.write('effect\n')
        return 'ran'
    spec=ToolSpec('project','tests','bounded tests',effect,{'target':ArgSpec(required=False)},command_execution=True)
    ready.put(True)
    if not start.wait(10):results.put('start-timeout');return
    try:results.put(invoke(store,spec,grant))
    except ValueError:results.put('refused')


def test_spawned_processes_share_one_atomic_use_budget(tmp_path):
    import multiprocessing
    store,spec,_,_=fixture(tmp_path)
    grant=issue(store,spec,uses=3)
    context=multiprocessing.get_context('spawn')
    ready,results=context.Queue(),context.Queue()
    start=context.Event();effect_path=tmp_path/'effects.txt'
    processes=[context.Process(target=_process_consume,args=(str(store.path),grant,ready,start,results,str(effect_path))) for _ in range(6)]
    try:
        for process in processes:process.start()
        for _ in processes:assert ready.get(timeout=15)
        start.set()
        outcomes=[results.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(15);assert not process.is_alive();assert process.exitcode==0
        assert outcomes.count('ran')==3
        assert outcomes.count('refused')==3
        assert effect_path.read_text().splitlines()==['effect']*3
        assert store.inspect(owner='owner-a')[0]['remaining']==0
    finally:
        start.set()
        for process in processes:
            if process.is_alive():process.terminate();process.join(3)
        ready.close();results.close()


def test_failed_execution_spends_attempt_and_parallel_budget_is_atomic(tmp_path):
    calls=[]
    def broken(r):calls.append(1);raise RuntimeError('fixture error')
    store,spec,_,_=fixture(tmp_path,broken)
    grant=issue(store,spec,uses=2)
    def attempt(_):
        try:invoke(store,spec,grant)
        except (ValueError,RuntimeError):pass
    with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(attempt,range(12)))
    assert len(calls)==2
    assert store.inspect(owner='owner-a')[0]['remaining']==0


def test_revoke_serializes_with_dispatch_and_returns_after_inflight(tmp_path):
    entered,release,revoked=threading.Event(),threading.Event(),threading.Event()
    def effect(r):entered.set();assert release.wait(3);return 'complete'
    store,spec,_,_=fixture(tmp_path,effect)
    grant=issue(store,spec)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running=pool.submit(invoke,store,spec,grant)
        assert entered.wait(3)
        def revoke():store.revoke(grant['id'],owner='owner-a');revoked.set()
        revoking=pool.submit(revoke)
        assert not revoked.wait(.05)
        release.set();assert running.result()=='complete';revoking.result()
    with pytest.raises(ValueError):invoke(store,spec,grant)


def test_shared_deny_cannot_be_overridden_by_grant(tmp_path,monkeypatch):
    calls=[]
    store,spec,_,_=fixture(tmp_path,lambda r:calls.append(1))
    grant=issue(store,spec)
    monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES','core.project.tests')
    with pytest.raises(ValueError):invoke(store,spec,grant)
    assert calls==[]


@pytest.mark.parametrize('setting,value',[('PROMETHEUS_DENY_CAPABILITIES','ui.chat'),('PROMETHEUS_DENY_SCOPES','maintenance.source.dispatch:'+ 'a'*64)])
def test_effective_policy_change_invalidates_pending_review_and_active_grant(tmp_path,monkeypatch,setting,value):
    from prometheus_assistant.application_policy import revision
    calls=[]
    store,spec,_,_=fixture(tmp_path,lambda r:calls.append(1))
    store.policy_revision=revision
    grant=issue(store,spec)
    review=store.review(spec,{'target':'tests'},owner='owner-a',seconds=60,uses=1)
    before=revision();monkeypatch.setenv(setting,value)
    assert revision()!=before
    with pytest.raises(ValueError):store.issue(review['review_id'],review['scope_sha256'],owner='owner-a')
    with pytest.raises(ValueError):invoke(store,spec,grant)
    assert calls==[]
    assert store.inspect(owner='owner-a')[0]['state']=='policy_invalid'
    normalized=revision();monkeypatch.setenv(setting,' '+value+','+value+' ')
    assert revision()==normalized


@pytest.mark.parametrize('transaction', [1,2])
def test_expiry_during_ledger_acquisition_refuses_before_effect(tmp_path,monkeypatch,transaction):
    from contextlib import contextmanager
    calls=[]
    store,spec,clock,_=fixture(tmp_path,lambda r:calls.append(1))
    grant=issue(store,spec,seconds=2,uses=1)
    original=store._db;count=[0]
    @contextmanager
    def waited():
        count[0]+=1
        if count[0]==transaction:
            # Deterministic equivalent of the file lock releasing after expiry.
            clock[0]+=3;clock[1]+=3
        with original() as db:yield db
    monkeypatch.setattr(store,'_db',waited)
    with pytest.raises(ValueError):invoke(store,spec,grant)
    assert calls==[]
    row=store.inspect(owner='owner-a')[0]
    assert row['state']=='expired'
    assert row['remaining']==(1 if transaction==1 else 0)
