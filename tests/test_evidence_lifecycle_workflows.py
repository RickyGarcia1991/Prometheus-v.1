"""Real history reads preserve conflict evidence without granting or replaying work."""
import hashlib
import pytest

from test_grants_http import endpoint
from test_shared_capability_evidence import complete, approved, O, B, JOB, REQUEST
from prometheus_assistant.action_approvals import ActionApprovals
from prometheus_assistant.decision_center import build_snapshot


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_conflict_outside_history_page_cannot_hide_unrelated_results_or_replay(tmp_path):
    app=ActionApprovals(history_root=tmp_path)
    first=complete(app)
    for n in range(51):
        approved(app, kind='runtime_config', marker=str(n))
    conflict=complete(app,status='checks_failed')
    unrelated=complete(app,job='9'*32,request='8'*64)
    before=files(tmp_path)
    rows={r['id']:r for r in app.history(owner=O)}
    assert first['id'] not in rows and len(rows)==50
    projection=rows[conflict['id']]['evidence_lifecycle']
    assert projection['state']=='conflicting'
    assert projection['related_decisions']==[dict(id=first['id'],fingerprint=first['fingerprint'])]
    assert rows[conflict['id']]['shared_evidence']==[]
    assert rows[unrelated['id']]['evidence_lifecycle']['state']=='recorded'
    assert rows[conflict['id']]['capability_outcomes'][-1]['status']=='checks_failed'
    snapshot=build_snapshot(decisions=list(rows.values()))
    states={r['state'] for r in snapshot['items'] if r['source']=='decisions'}
    assert 'conflicting' in states and 'verified' in states
    reopened=ActionApprovals(history_root=tmp_path)
    assert reopened.history(owner=O)==list(rows.values())
    assert reopened.peek(owner=O,binding=B) is None
    assert files(tmp_path)==before


def test_http_conflict_is_owner_bound_read_only_and_keeps_raw_observations(endpoint):
    app,gate,effects,request=endpoint
    with gate.grant_owner('fixture-cookie') as owner:
        first=complete(app.action_approvals,owner=owner)
        second=complete(app.action_approvals,owner=owner,status='checks_failed')
        unrelated=complete(app.action_approvals,owner=owner,job='9'*32,request='8'*64)
    other=complete(app.action_approvals,owner='0'*64,status='tool_failed')
    root=app.action_approvals.decision_history.root
    before=files(root)
    for kwargs,expected in [({'owner':False},401),({'token':False},403),({'origin':'https://foreign.example'},403)]:
        assert request('/api/decisions/history',**kwargs)[0]==expected
    code,data=request('/api/decisions/history')
    assert code==200 and data['read_only']
    rows={r['id']:r for r in data['items']}
    assert set(rows)=={first['id'],second['id'],unrelated['id']}
    assert [rows[r['id']]['evidence_lifecycle']['state'] for r in (first,second,unrelated)]==['conflicting','conflicting','recorded']
    assert rows[first['id']]['capability_outcomes'][-1]['status']=='checks_passed'
    assert rows[second['id']]['capability_outcomes'][-1]['status']=='checks_failed'
    assert other['id'] not in str(data) and owner not in str(data)
    assert not effects and files(root)==before
    app.action_approvals=ActionApprovals(history_root=root)
    assert request('/api/decisions/history')[1]==data
    assert app.action_approvals.peek(owner=owner,binding=hashlib.sha256(b'fixture-cookie').hexdigest()) is None
    assert files(root)==before and not effects


@pytest.mark.parametrize('limit',[0,51,True,None,1.0,'1'])
def test_page_limit_still_rejected_before_reading_history(tmp_path,monkeypatch,limit):
    app=ActionApprovals(history_root=tmp_path)
    def forbidden():raise AssertionError('Invalid page read its ledger')
    monkeypatch.setattr(app.decision_history,'_all_rows',forbidden)
    with pytest.raises(ValueError,match='limit'):
        app.history(owner=O,limit=limit)


def test_page_and_projection_reuse_one_canonical_snapshot(tmp_path,monkeypatch):
    app=ActionApprovals(history_root=tmp_path)
    first=complete(app);second=complete(app,status='checks_failed')
    original=app.decision_history._all_rows;reads=[]
    def once():
        reads.append(1)
        if len(reads)>1:raise AssertionError('History/projection reread the merged ledger')
        return original()
    monkeypatch.setattr(app.decision_history,'_all_rows',once)
    rows=app.history(owner=O,limit=1)
    assert len(reads)==1 and len(rows)==1 and rows[0]['id']==second['id']
    assert rows[0]['evidence_lifecycle']['state']=='conflicting'
    assert rows[0]['evidence_lifecycle']['related_decisions']==[dict(id=first['id'],fingerprint=first['fingerprint'])]
