"""Pure authority can expire while the lifecycle transaction is acquired."""
from contextlib import contextmanager
import pytest
from test_evidence_followups import f
from prometheus_assistant.evidence_interpretation_lifecycle import EvidenceInterpretationLifecycle


@pytest.mark.parametrize('changed',['cancelled','policy'])
def test_guard_rechecks_local_authority_before_claim(f,monkeypatch,changed):
    row=f.prepare();f.decide(row)
    guard=EvidenceInterpretationLifecycle.claim_guard
    @contextmanager
    def expired(self,*args,**kwargs):
        with guard(self,*args,**kwargs) as state:
            if changed=='cancelled':f.stopped=True
            else:
                monkeypatch.setattr('prometheus_assistant.scheduled_syntax.revision',lambda:'f'*64)
                monkeypatch.setattr('prometheus_assistant.evidence_followups.revision',lambda:'f'*64)
            yield state
    monkeypatch.setattr(EvidenceInterpretationLifecycle,'claim_guard',expired)
    result=f.controller.run_one()
    assert result['state']=='cancelled'
    assert f.store.queue.get(row['payload']['request']['job_id'])['state']=='cancelled'
    assert not f.executions
