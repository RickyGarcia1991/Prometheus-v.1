"""Actual bounded specialist records and measured history must fit together."""
from copy import deepcopy
import json

import pytest

from prometheus_assistant import memory, measured_review_runtime as runtime
from test_measured_review_runtime import rig
from test_project_specialists import team, run, claim


def test_aggregate_budget_summarizes_measurements_without_losing_specialists(team, rig):
    rows=[claim(message=str(i)+' '+('\u754c'*1800)) for i in range(8)]
    team.rows=dict(correctness=rows,test_coverage=rows)
    specialist=run(team)
    measured=runtime.run_review(rig.app,rig.candidate,rig.packet)
    first=measured['report']['measured_checks'][0]
    checks=[]
    for index in range(32):
        row=deepcopy(first);row['evidence_id']=format(index,'016x');checks.append(row)
    measured['report'].update(measured_checks=checks,failed_checks=deepcopy(checks),
        reviewer_omissions=[c['evidence_id'] for c in checks])
    assert runtime.validate_saved_review(measured)['report'] is not None
    details=dict(project_edit=dict(status='failed',specialist_review=specialist,measured_review=measured))
    assert len(json.dumps(details,ensure_ascii=False,separators=(',',':')).encode())>memory.MAX_TURN_METADATA_BYTES
    saved=json.loads(memory._response_metadata(details))
    assert saved['project_edit']['specialist_review']==specialist
    summary=saved['project_edit']['measured_review']['history_summary']
    assert summary['status']=='checks_failed'
    assert summary['failed_check_count']==summary['reviewer_omission_count']==32
    assert summary['may_present_as_checks_passed'] is False
    assert len(memory._response_metadata(saved).encode())<=memory.MAX_TURN_METADATA_BYTES


def test_other_oversized_metadata_still_rejected(rig):
    measured=runtime.run_review(rig.app,rig.candidate,rig.packet)
    details=dict(project_edit=dict(measured_review=measured),results=[
        dict(excerpt='x'*16384,summary='y'*16384,description='z'*16384) for _ in range(5)])
    with pytest.raises(ValueError,match='storage bound'):
        memory._response_metadata(details)
