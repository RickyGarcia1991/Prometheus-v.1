"""Owner-bound durable history is a read-only record, never a replay route."""
from test_grants_http import endpoint
import hashlib

ROUTE='/api/decisions/history'


def test_history_auth_and_restart_without_restoring_approval(endpoint):
    app,gate,effects,request=endpoint
    binding=hashlib.sha256(b'fixture-cookie').hexdigest()
    with gate.grant_owner('fixture-cookie') as owner:
        row=app.action_approvals.review(owner=owner,binding=binding,session='a'*32,
            kind='project_edit',payload={'secret':'PRIVATE_PROPOSED_SOURCE'},review={},policy_revision='fixture')
        app.action_approvals.consume(owner=owner,binding=binding,session=row['session'],
            approval_id=row['id'],fingerprint=row['fingerprint'],policy_revision='fixture',decision='request_changes',feedback='Revise the tests.')
    for kwargs,expected in [({'owner':False},401),({'token':False},403),({'origin':'https://foreign.example'},403)]:
        assert request(ROUTE,**kwargs)[0]==expected
    code,data=request(ROUTE)
    assert code==200 and data['read_only'] and len(data['items'])==1
    assert data['items'][0]['feedback']=='Revise the tests.'
    assert owner not in str(data) and 'PRIVATE_PROPOSED_SOURCE' not in str(data) and not effects
    from prometheus_assistant.action_approvals import ActionApprovals
    app.action_approvals=ActionApprovals(history_root=app.data_root/'Maintenance/DecisionHistory')
    assert request(ROUTE)[1]==data
    assert app.action_approvals.pending(owner=owner,binding=binding) is None
    assert app.action_approvals.history(owner='0'*64)==[]


def test_missing_history_is_empty_but_corrupt_history_is_unavailable(endpoint,monkeypatch):
    app,gate,effects,request=endpoint
    assert request(ROUTE)[1]['items']==[]
    def broken(**kwargs):raise ValueError('private database location')
    monkeypatch.setattr(app.action_approvals,'history',broken)
    code,data=request(ROUTE)
    assert code==503 and 'private' not in str(data)
    assert not effects
