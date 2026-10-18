import copy
import json

import pytest

from prometheus_assistant.decision_center import MAX_ACTIVITY, MAX_JOBS, NAVIGATION, build_snapshot


def snapshot(**kwargs):
    data = dict(approval=None, jobs=[], activity=[], resources={'mode':'Balanced'}, now=1000.)
    data.update(kwargs)
    return build_snapshot(**data)


def test_summary_never_copies_private_material_or_exposes_actions():
    secret = 'PRIVATE-PROMPT-SOURCE-PATH-COMMAND'
    inputs = dict(approval={'kind':'project_edit','id':secret,'payload':{'content':secret},'review':{'action':secret}},
        jobs=[{'state':'waiting','expires_at':2000,'reasons':[secret],'files':[secret],'request':{'content':secret},'target':secret}],
        activity=[{'state':'failed','prompt':secret,'message':secret,'stage':secret,'result':secret}],
        resources={'mode':'Balanced','error':secret,'observations':{'path':secret}},now=1000)
    before = copy.deepcopy(inputs)
    result = build_snapshot(**inputs)
    assert inputs == before
    assert secret not in json.dumps(result)
    assert result['read_only'] is True
    assert all(set(row)=={'id','kind','label','severity','state','count','source','freshness','navigation'} for row in result['items'])
    assert all(row['navigation'] in NAVIGATION for row in result['items'])


def test_groups_equivalent_rows_and_orders_deterministically():
    jobs = [{'state':'waiting','expires_at':2000}]*3 + [{'state':'unconfirmed'}, {'state':'completed','result':{'evidence':{'status':'failed'}}}]
    one = snapshot(jobs=jobs)
    assert one == snapshot(jobs=list(reversed(jobs)))
    assert one['items'][0]['state']=='unconfirmed'
    assert one['items'][0]['severity']=='critical'
    assert 'Never rerun' in one['items'][0]['label']
    waiting = next(row for row in one['items'] if row['state']=='waiting')
    assert waiting['count']==3
    assert any(row['state']=='failed' and row['severity']=='warning' for row in one['items'])


@pytest.mark.parametrize('state',['unconfirmed','claimed'])
def test_uncertain_claim_never_reclassified_as_success(state):
    result=snapshot(jobs=[{'state':state,'result':{'evidence':{'status':'passed'}}}])
    row=next(row for row in result['items'] if row['source']=='queue')
    assert row['state']=='unconfirmed' and row['severity']=='critical'


def test_completed_without_verification_does_not_claim_pass():
    result=snapshot(jobs=[{'state':'completed','result':{}}])
    row=next(row for row in result['items'] if row['source']=='queue')
    assert row['state']=='unknown' and row['severity']=='warning'


def test_expiry_is_projected_without_mutating_saved_record():
    job={'state':'waiting','expires_at':999,'next_attempt_at':1001}
    result=snapshot(jobs=[job])
    assert job['state']=='waiting'
    assert any(row['state']=='expired' for row in result['items'])


def test_monotonic_approval_expiry_not_compared_with_wall_clock():
    result=snapshot(approval={'kind':'project_task','expires_at':12})
    assert result['sources']['approvals']['freshness']=='saved_records'
    assert any(row['state']=='pending' for row in result['items'])


@pytest.mark.parametrize('kind,route',[('project_edit','project'),('scheduled_syntax','scheduled'),('remote_syntax','remote'),('self_improve','self_improvement'),('integration','integrations'),('integration_access','integrations'),('idle_config','research'),('unknown-private-kind','activity')])
def test_pending_navigation_is_fixed(kind,route):
    result=snapshot(approval={'kind':kind})
    row=next(row for row in result['items'] if row['source']=='approvals')
    assert row['navigation']==route
    if kind=='unknown-private-kind': assert kind not in json.dumps(result)


def test_new_saved_reads_and_live_freshness_are_distinct():
    result=snapshot()
    for source in ['approvals','queue','activity']:
        assert result['sources'][source]['state']=='available'
        assert result['sources'][source]['freshness']=='saved_records'
    assert result['sources']['resources']['freshness']=='unknown'


@pytest.mark.parametrize('observed,expires,expected',[(990,1010,'current'),(900,950,'stale'),(1100,1200,'unknown'),(1001,999,'unknown'),(None,None,'unknown')])
def test_resource_freshness_requires_explicit_valid_interval(observed,expires,expected):
    result=snapshot(resources={'mode':{'effective':'Battery Saver'},'observed_at':observed,'expires_at':expires})
    assert result['sources']['resources']['freshness']==expected
    assert any(row['state']=='stale' for row in result['items']) == (expected=='stale')


def test_bound_and_explicit_truncation():
    result=snapshot(jobs=[{'state':'waiting'}]*(MAX_JOBS+1),activity=[{'state':'done'}]*(MAX_ACTIVITY+3))
    assert result['sources']['queue']['omitted']==1
    assert result['sources']['activity']['omitted']==3
    assert result['sources']['queue']['truncated']
    assert next(row for row in result['items'] if row['state']=='waiting')['count']==100
    assert next(row for row in result['items'] if row['source']=='activity' and row['state']=='completed')['count']==50
    assert len(result['items']) < 10


@pytest.mark.parametrize('bad',[None,'private',True,10,{}])
def test_unavailable_lists_are_not_reported_as_empty_success(bad):
    result=snapshot(jobs=bad,activity=bad)
    assert result['sources']['queue']['state']=='unavailable'
    assert result['sources']['activity']['state']=='unavailable'


def test_malformed_entries_cannot_leak_or_crash():
    result=snapshot(approval={'unavailable':True,'error':'private'},jobs=[None,{'state':[]},{}],activity=[None,{'status':{}}],resources={'mode':[]},now=float('nan'))
    assert result['generated_at'] is None
    assert result['sources']['queue']['state']=='partial'
    assert result['sources']['resources']['state']=='unavailable'
    assert result['sources']['approvals']['state']=='unavailable'
    assert 'private' not in json.dumps(result)
    json.dumps(result,allow_nan=False)


def test_critical_mode_visible_even_without_sample_timestamp():
    result=snapshot(resources={'mode':'Critical Recovery'})
    row=next(row for row in result['items'] if row['source']=='resources')
    assert row['severity']=='critical' and row['freshness']=='unknown'


def test_recent_review_is_attention_not_unknown():
    result=snapshot(activity=[{'state':'review','stage':'private details'}])
    row=next(row for row in result['items'] if row['source']=='activity')
    assert row['state']=='review' and row['severity']=='warning'
    assert row['label']=='Recent activity needs review.'
    assert 'private' not in json.dumps(result)
