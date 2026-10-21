"""Real HTTP authorization and history for bounded follow-up checks.

The worker adapter is an inert generated fixture; no enrolled worker is contacted.
"""
from contextlib import nullcontext
from types import SimpleNamespace
import copy
import time

import pytest
from prometheus_assistant import ui_server as ui, scheduled_syntax_ui
from prometheus_assistant.application_policy import revision
from test_grants_http import endpoint
from test_shared_capability_evidence import complete
from test_scheduled_syntax_ui import receipt


@pytest.mark.parametrize('choice', ['approve', 'reject'])
def test_http_followup_exact_review_result_and_saved_history(endpoint, monkeypatch, tmp_path_factory, choice):
    app, gate, effects, request = endpoint
    project = tmp_path_factory.mktemp('followup-http-project')
    (project/'generated.py').write_bytes(b'answer = 42\n')
    calls = []
    def execute(req):
        calls.append(copy.deepcopy(req))
        return receipt(req)
    adapter = SimpleNamespace(
        target=lambda: dict(account_id='fixture-account', device_id='fixture-surface',
            device_name='Generated Surface fixture', worker_digest='b'*64,
            configuration_digest='c'*64, online=True),
        admission=lambda req=None: dict(decision='ready', reasons=[], authenticated=True,
            observed_at=time.time(), expires_at=time.time()+25,
            observations=dict(available_ram_gib=4, free_disk_gib=10, cpu_percent=10, plugged_in=True)),
        execute=execute, recover=lambda req: None)
    monkeypatch.setattr(scheduled_syntax_ui, 'adapters', lambda app, owner: [adapter])
    monkeypatch.setattr(ui, 'reference_session', nullcontext)
    with gate.grant_owner('fixture-cookie') as owner:
        original = complete(app.action_approvals, owner=owner)
        complete(app.action_approvals, owner=owner, status='checks_failed')
        history = app.action_approvals.history(owner=owner)
        conflict = next(r for r in history if r['id']==original['id'])['evidence_conflict']
        base = app.action_approvals.prepare_evidence_resolution(owner=owner, binding='c'*64,
            session='1'*32, target_id=original['id'], target_fingerprint=original['fingerprint'],
            snapshot_sha256=conflict['snapshot_sha256'], fact_sha256=conflict['variants'][0]['fact_sha256'],
            reason='LABELED HTTP FIXTURE', policy_revision=revision())
        app.action_approvals.decide_evidence_resolution(owner=owner, binding='c'*64,
            session=base['session'], approval_id=base['id'], fingerprint=base['fingerprint'],
            decision='approve', policy_revision=revision())
        item = app.action_approvals._interpretation_change_inputs(owner, base['id'], base['fingerprint'])[0]
    body = dict(mode='evidence_followup_prepare', project=str(project), remote_files='generated.py',
        followup_interpretation_id=base['id'], followup_interpretation_fingerprint=base['fingerprint'],
        followup_state_sha256=item['state_sha256'], followup_snapshot_sha256=conflict['snapshot_sha256'],
        followup_fact_sha256=item['effective_payload']['fact_sha256'], followup_reason='Check these saved files.',
        followup_operation='python-syntax-check', followup_placement='remote-lightweight-v1')
    for kwargs, expected in [({'owner':False},401),({'token':False},403),({'origin':'https://foreign.example'},403)]:
        assert request('/api/jobs', body, **kwargs)[0] == expected
    assert request('/api/evidence/followups', owner=False)[0] == 401
    assert request('/api/evidence/followups', origin='https://foreign.example')[0] == 403
    assert request('/api/jobs', body | {'command':'arbitrary'})[0] == 400
    with pytest.raises(ValueError, match='authenticated'):
        app.execute(body)
    def submit(value):
        code, job = request('/api/jobs', value)
        assert code == 202, job
        end = time.monotonic()+8
        while time.monotonic()<end:
            code, result = request('/api/job/'+job['id'])
            assert code == 200
            if result['state']!='running': break
            time.sleep(.01)
        assert result['state']=='done', result
        return result['result']
    pending = submit(body)['details']['approval']
    assert pending['kind']=='evidence_followup' and not calls
    decided = submit(dict(mode='approval_decision', session=pending['session'],
        approval_id=pending['id'], approval_fingerprint=pending['fingerprint'], approval_decision=choice))
    job = decided['details']['evidence_followup']
    assert job['state'] == ('completed' if choice=='approve' else 'rejected')
    assert len(calls) == (1 if choice=='approve' else 0)
    code, saved = request('/api/evidence/followups')
    assert code==200 and saved['read_only']
    assert saved['jobs'][0]['approval_id']==pending['id']
    code, history = request('/api/decisions/history')
    assert code==200
    original_row = next(r for r in history['items'] if r['id']==original['id'])
    assert original_row['evidence_conflict']==conflict
    assert original_row['evidence_followups']['items'][0]['job_id']==job['job_id']
    assert next(r for r in history['items'] if r['id']==pending['id'])['kind']=='other'
    # Explicit inspection reads the saved result without arming or dispatching it.
    before_history = copy.deepcopy(history)
    code, assessment = request('/api/evidence/followups/assess',
        dict(job_id=job['job_id'], project=str(project)))
    assert code == 200, assessment
    assert assessment['read_only'] and not assessment['authorizes_execution']
    assert assessment['applicability'] == ('matching_inputs' if choice=='approve' else 'unavailable')
    assert assessment['historical']['check_status'] == ('passed' if choice=='approve' else 'unavailable')
    assert request('/api/decisions/history')[1] == before_history
    assert len(calls) == (1 if choice=='approve' else 0)
    assert request('/api/evidence/followups/assess',
        dict(job_id=job['job_id'], project=str(project.parent)))[0] == 400
    # A locked owner cannot read this queue or resume its jobs.
    assert request('/api/owner/lock', {})[0]==200
    assert request('/api/evidence/followups')[0]==401
    assert request('/api/jobs', {'mode':'evidence_followup_tick'})[0]==401
    assert len(calls)==(1 if choice=='approve' else 0) and not effects
    assert (project/'generated.py').read_bytes()==b'answer = 42\n'


def test_followup_asset_served_by_actual_server(tmp_path):
    import http.client
    import threading
    app=ui.Interface(tmp_path/'data/memory.sqlite3')
    server=ui.Server(('127.0.0.1',0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    try:
        conn.request('GET','/evidence-followups.js',headers={'Host':server.owner_origin.removeprefix('http://')})
        response=conn.getresponse()
        assert response.status==200
        assert response.getheader('Content-Type').startswith('text/javascript')
        assert response.read()==(ui.ASSETS/'evidence-followups.js').read_bytes()
    finally:
        conn.close();server.shutdown();server.server_close();thread.join(3);app.close()
