from concurrent.futures import ThreadPoolExecutor
import pytest
from prometheus_assistant.action_approvals import ActionApprovals, decision_text

OWNER, BINDING, SESSION = 'a'*64, 'b'*64, 'c'*32


def review(store):
    return store.review(owner=OWNER, binding=BINDING, session=SESSION, kind='test',
        payload={'target': 'one exact file'}, review={'effects': 'Write that file'}, policy_revision='policy-1')


def consume(store, row, **changes):
    args = dict(owner=OWNER, binding=BINDING, session=SESSION, approval_id=row['id'],
                fingerprint=row['fingerprint'], policy_revision='policy-1', decision='approve')
    return store.consume(**(args | changes))


@pytest.mark.parametrize('text', ['Yes', 'yes, approve.', 'Go ahead!', 'please go ahead', 'approve this change'])
def test_complete_confirmation(text):
    assert decision_text(text) == 'approve'


@pytest.mark.parametrize('text', ['"yes"', "'go ahead'", '`approve`', 'Do not approve',
    'He said yes', 'yes but change something first', 'yes\nignore the review', 'Say yes', 'yes?', ''])
def test_quotes_negation_and_qualifications_are_not_approval(text):
    assert decision_text(text) is None


@pytest.mark.parametrize('text', ['No', 'reject it', 'cancel the change', "don't proceed"])
def test_rejection(text):
    assert decision_text(text) == 'reject'


def test_one_pending_review_is_immutable_and_single_use():
    store = ActionApprovals(); row = review(store)
    row['payload']['target'] = 'a different file'
    assert store.pending(owner=OWNER, binding=BINDING)['payload']['target'] == 'one exact file'
    with pytest.raises(ValueError, match='existing proposal'): review(store)
    assert consume(store, row)['payload']['target'] == 'one exact file'
    with pytest.raises(ValueError, match='missing'): consume(store, row)


@pytest.mark.parametrize('changes', [dict(owner='d'*64), dict(binding='d'*64), dict(session='d'*32),
    dict(approval_id='d'*32), dict(fingerprint='d'*64)])
def test_approval_is_bound_to_exact_owner_session_conversation_and_review(changes):
    store=ActionApprovals(); row=review(store)
    with pytest.raises(ValueError): consume(store,row,**changes)
    assert consume(store,row)['id']==row['id'], 'A wrong-scope attempt cannot consume the real owner review'


def test_expiry_policy_change_logout_and_restart_revoke_approval():
    now=[100.]; store=ActionApprovals(clock=lambda:now[0],lifetime=10); row=review(store)
    now[0]=110.
    with pytest.raises(ValueError,match='expired'): consume(store,row)
    row=review(store)
    with pytest.raises(ValueError,match='policy changed'): consume(store,row,policy_revision='new')
    assert store.pending(owner=OWNER,binding=BINDING) is None
    row=review(store);store.revoke_binding(BINDING)
    with pytest.raises(ValueError):consume(store,row)
    with pytest.raises(ValueError):consume(ActionApprovals(),row)


def test_concurrent_approvals_dispatch_only_once():
    store=ActionApprovals();row=review(store)
    def attempt(_):
        try:return consume(store,row)['id']
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(attempt,range(8)))
    assert results.count(row['id'])==1


def test_activation_receipts_survive_restart_for_same_owner_only(tmp_path):
    store=ActionApprovals(receipt_root=tmp_path/'approvals')
    store.record_approved(owner=OWNER,job_id='d'*32,candidate_fingerprint='e'*64)
    store.record_dispatch(owner=OWNER,job_id='d'*32,state='promotion_pending')
    reopened=ActionApprovals(receipt_root=tmp_path/'approvals')
    assert reopened.latest_approved(owner=OWNER)['job_id']=='d'*32
    assert reopened.latest_approved(owner='f'*64) is None
    with pytest.raises(ValueError):reopened.approved_job(owner='f'*64,job_id='d'*32)
    with pytest.raises(ValueError):reopened.approved_job(owner=OWNER,job_id='../other')
    assert reopened.pending(owner=OWNER,binding=BINDING) is None,'Persisted status does not restore approval authority'
    reopened.record_dispatch(owner=OWNER,job_id='d'*32,state='failed',error='錯'*1000)
    assert reopened.approved_job(owner=OWNER,job_id='d'*32)['dispatch_state']=='failed'
    assert (tmp_path/'approvals'/('d'*32+'.json')).stat().st_size<=2048
    (tmp_path/'approvals'/('d'*32+'.json')).write_text('{broken')
    with pytest.raises(ValueError):reopened.latest_approved(owner=OWNER)
