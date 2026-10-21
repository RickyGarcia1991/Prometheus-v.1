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


def test_replacement_decision_and_outcome_survive_reopen_without_authority(setup):
    from prometheus_assistant.action_approvals import ActionApprovals
    from test_project_tasks_ui import decide
    s=setup;first=s.run(s.req);returned=s.run(decision(first))
    draft=returned['details']['revision_draft']
    revised=s.run({k:v for k,v in draft.items() if k!='origin_approval'}|
                  {'mode':'project_edit','session':first['session']})
    approval=revised['details']['approval']
    assert 'revision_token' not in approval['payload']['request']
    assert not s.app.action_approvals.history(owner=OWNER)[0]['revisions'][0]['child_state']=='recorded'
    decide(s,revised)
    history=s.app.action_approvals.history(owner=OWNER)
    child=next(row for row in history if row['id']==approval['id'])
    assert child['revisions'][0]['parent_id']==first['details']['approval']['id']
    assert child['revisions'][0]['child_state']=='recorded'
    assert child['outcomes'][-1]['status']=='passed'
    reopened=ActionApprovals(history_root=s.app.action_approvals.decision_history.root)
    assert reopened.history(owner=OWNER)==history
    assert reopened.pending(owner=OWNER,binding=BINDING) is None
    with pytest.raises(ValueError):
        reopened.validate_revision_context(owner=OWNER,binding=BINDING,session=first['session'],
            project_hash='a'*64,revision_token=draft['revision_token'])


def test_revision_context_is_not_saved_in_requests_or_details(setup,monkeypatch):
    import json
    s=setup;saved=[];original=s.app.save
    def save(request,details,*args,**kwargs):
        saved.append((request,details))
        return original(request,details,*args,**kwargs)
    monkeypatch.setattr(s.app,'save',save)
    first=s.run(s.req);draft=s.run(decision(first))['details']['revision_draft']
    s.run({k:v for k,v in draft.items() if k!='origin_approval'}|
          {'mode':'project_edit','session':first['session']})
    assert 'revision_token' not in json.dumps(saved)
    assert draft['revision_token'] not in json.dumps(saved)


@pytest.mark.parametrize('fault',['project','session','token','delegation','research'])
def test_invalid_revision_does_not_call_model_or_apply(setup,fault,tmp_path):
    s=setup;first=s.run(s.req);draft=s.run(decision(first))['details']['revision_draft']
    req={k:v for k,v in draft.items() if k!='origin_approval'}|{'mode':'project_edit','session':first['session']}
    if fault=='project':
        other=tmp_path/'other';other.mkdir();(other/'main.py').write_text('return 1\n')
        req['project']=str(other)
    elif fault=='session':req['session']='f'*32
    elif fault=='token':req['revision_token']='f'*32
    elif fault=='delegation':req['edit_policy']='delegated'
    else:req['research_query']='new public query'
    with pytest.raises(ValueError):s.run(req)
    assert len(s.calls)==1 and s.file.read_text()=='def value():\n    return 1\n'
    assert s.app.action_approvals.pending(owner=OWNER,binding=BINDING) is None
