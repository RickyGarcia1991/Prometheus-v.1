"""A live revision response belongs only to the authenticated submitting browser."""
from test_grants_http import endpoint
import hashlib


def test_observed_job_redacts_live_context_without_mutating_owner_result(endpoint):
    app, gate, effects, request = endpoint
    job_id = 'a'*24
    context = dict(kind='runtime_config', capability_revision_token='b'*32,
                   origin_approval='c'*32, session='d'*32, feedback='Use automatic startup.')
    app.jobs[job_id] = dict(id=job_id, state='done', result=dict(reply='Changes requested.',
        details=dict(approval_resolved='c'*32, capability_revision=context)))
    app._speech_owners[job_id] = dict(binding=hashlib.sha256(b'another-browser').hexdigest())
    code, result = request('/api/job/'+job_id)
    assert code == 200 and 'capability_revision' not in result['result']['details']
    assert app.jobs[job_id]['result']['details']['capability_revision'] == context
    app._speech_owners[job_id]['binding'] = hashlib.sha256(b'fixture-cookie').hexdigest()
    code, result = request('/api/job/'+job_id)
    assert code == 200 and result['result']['details']['capability_revision'] == context
    assert request('/api/job/'+job_id, owner=False)[0] == 401
    assert not effects
