"""Returned exact proposals lose authority; drafts never execute themselves."""
import pytest
from test_project_tasks_ui import setup, OWNER, BINDING
from prometheus_assistant import ui_server as ui


def decision(result, feedback='Keep the original behavior; add documentation.'):
    row=result['details']['approval']
    return dict(mode='approval_decision', approval_decision='request_changes',
        approval_id=row['id'], approval_fingerprint=row['fingerprint'],
        session=row['session'], approval_feedback=feedback)


def test_returned_proposal_does_not_execute_and_needs_fresh_review(setup):
    s=setup
    first=s.run(s.req)
    returned=s.run(decision(first))
    assert s.file.read_text()=='def value():\n    return 1\n'
    assert len(s.calls)==1
    assert s.app.action_approvals.pending(owner=OWNER,binding=BINDING) is None
    draft=returned['details']['revision_draft']
    assert draft['edit_policy']=='ask_first' and draft['research_query']==''
    assert 'Keep the original behavior' in draft['prompt']
    assert returned['details']['approval_resolved']==first['details']['approval']['id']
    with pytest.raises(ValueError):
        s.app.action_approvals.consume(owner=OWNER,binding=BINDING,session=first['session'],
            approval_id=first['details']['approval']['id'],fingerprint=first['details']['approval']['fingerprint'],
            policy_revision=first['details']['approval']['policy_revision'],decision='approve')
    revised=s.run({k:v for k,v in draft.items() if k!='origin_approval'}|{'mode':'project_edit','session':first['session']})
    assert revised['details']['approval']['id']!=first['details']['approval']['id']
    assert s.file.read_text()=='def value():\n    return 1\n'
    receipt=s.app.action_approvals.history(owner=OWNER)[0]
    assert receipt['decision']=='request_changes' and receipt['feedback']=='Keep the original behavior; add documentation.'


@pytest.mark.parametrize('feedback',['', ' ', 'x'*2001, '\x00', None])
def test_invalid_feedback_preserves_pending(setup,feedback):
    s=setup;result=s.run(s.req)
    with pytest.raises(ValueError):s.run(decision(result,feedback))
    assert s.app.action_approvals.pending(owner=OWNER,binding=BINDING)['id']==result['details']['approval']['id']


def test_oversized_combined_goal_preserves_pending(setup):
    s=setup;result=s.run(s.req|{'prompt':'x'*3500})
    with pytest.raises(ValueError,match='4000'):s.run(decision(result,'y'*1000))
    assert s.app.action_approvals.pending(owner=OWNER,binding=BINDING)


def test_feedback_is_not_accepted_on_other_requests():
    with pytest.raises(ValueError):ui.validate_job(dict(mode='chat',prompt='hello',approval_feedback='change it'))
    with pytest.raises(ValueError):ui.validate_job(dict(mode='approval_decision',approval_decision='approve',
        approval_id='a'*32,approval_fingerprint='b'*64,approval_feedback='change it'))


def test_public_query_is_not_carried_to_revision_draft(setup):
    s=setup;result=s.run(s.req)
    # The original reviewed payload can contain prior research; returning it must not send it again.
    key=(OWNER,BINDING)
    s.app.action_approvals._pending[key]['payload']['request']['research_query']='old public query'
    draft=s.run(decision(result))['details']['revision_draft']
    assert draft['research_query']==''
