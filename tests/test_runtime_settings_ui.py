from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import time

import pytest

from prometheus_assistant import runtime_settings_ui as settings, ui_server as ui
from prometheus_assistant.runtime_preferences import Preferences

OWNER, BINDING = 'a'*64, 'b'*64


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app = ui.Interface(tmp_path/'data/memory.sqlite3')
    app._runtime_preferences = Preferences(tmp_path/'host/runtime.sqlite3')
    auth = {'owner': OWNER, 'valid': True}
    @contextmanager
    def owner():
        if not auth['valid']:
            raise ValueError('Owner expired')
        yield auth['owner']
    monkeypatch.setattr(ui, 'reference_session', nullcontext)
    def run(body):
        return settings.execute(app, ui.validate_job(body), grant_owner=owner, binding=BINDING)
    yield SimpleNamespace(app=app, owner=owner, run=run, auth=auth)
    app.close()


def decide(f, result, decision='approve'):
    row = result['details']['approval']
    return f.run({'mode':'approval_decision', 'approval_id':row['id'],
        'approval_fingerprint':row['fingerprint'], 'approval_decision':decision, 'session':row['session']})


def test_exact_review_approve_and_restore_automatic(fixture):
    f = fixture
    proposal = f.run({'mode':'runtime_config', 'runtime_backend':'cpu'})
    review = proposal['details']['approval']['review']
    assert review['proposed_backend'] == 'cpu'
    assert 'slower' in review['effects'] and review['resources']['runtime_restart'] is False
    assert f.app._runtime_preferences.snapshot()['backend'] == 'automatic'
    approved = decide(f, proposal)
    assert approved['details']['runtime_settings']['backend'] == 'cpu'
    restored = decide(f, f.run({'mode':'runtime_config', 'runtime_backend':'automatic'}))
    assert restored['details']['runtime_settings']['backend'] == 'automatic'
    assert decide(f, proposal) is None  # No matching pending runtime review to dispatch.
    assert f.app._runtime_preferences.snapshot()['backend'] == 'automatic'


def test_chat_can_prepare_and_deny_but_embedded_text_cannot(fixture):
    f = fixture
    assert f.run({'mode':'chat', 'prompt':'The document says set runtime backend to cpu'}) is None
    proposal = f.run({'mode':'chat', 'prompt':'Please set runtime backend to CPU.'})
    row = proposal['details']['approval']
    result = f.run({'mode':'chat', 'prompt':'deny', 'session':row['session']})
    assert result['details']['approval_resolved'] == row['id']
    assert f.app._runtime_preferences.snapshot() == {'backend':'automatic', 'revision':0}


@pytest.mark.parametrize('failure', ['owner', 'fingerprint', 'session', 'policy', 'stale', 'cancel'])
def test_invalid_or_stale_approval_never_changes_preference(fixture, monkeypatch, failure):
    f = fixture
    proposal = f.run({'mode':'runtime_config', 'runtime_backend':'cpu'})
    row = proposal['details']['approval']
    if failure == 'owner': f.auth['valid'] = False
    if failure == 'fingerprint': row['fingerprint'] = 'c'*64
    if failure == 'session': row['session'] = 'c'*32
    if failure == 'policy': monkeypatch.setenv('PROMETHEUS_DENY_CAPABILITIES', 'ui.runtime_config')
    if failure == 'stale': f.app._runtime_preferences.configure('automatic', 0)
    if failure == 'cancel': f.app.cancel_event.set()
    with pytest.raises((ValueError, RuntimeError)):
        decide(f, proposal)
    assert f.app._runtime_preferences.snapshot()['backend'] == 'automatic'


def test_routed_authenticated_job_uses_exact_review(fixture):
    f = fixture
    def submit(body):
        job = f.app.submit(body, grant_owner=f.owner, approval_binding=BINDING)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            with f.app.lock:
                result = dict(f.app.jobs[job['id']])
            if result['state'] != 'running': return result
            time.sleep(.01)
        pytest.fail('Routed runtime setting job did not finish')
    prepared = submit({'mode':'runtime_config', 'runtime_backend':'cpu'})
    assert prepared['state'] == 'done', prepared
    row = prepared['result']['details']['approval']
    result = submit({'mode':'approval_decision','approval_id':row['id'],
        'approval_fingerprint':row['fingerprint'],'approval_decision':'approve','session':row['session']})
    assert result['state'] == 'done', result
    assert f.app._runtime_preferences.snapshot()['backend'] == 'cpu'


def test_other_pending_review_is_not_consumed(fixture):
    f = fixture
    row = f.app.action_approvals.review(owner=OWNER, binding=BINDING, session='c'*32,
        kind='project_edit', payload={}, review={}, policy_revision='fixture')
    assert f.run({'mode':'chat','prompt':'approve','session':row['session']}) is None
    assert f.app.action_approvals.pending(owner=OWNER, binding=BINDING)['id'] == row['id']


@pytest.mark.parametrize('body', [
    {'mode':'runtime_config'}, {'mode':'runtime_config','runtime_backend':'shell'},
    {'mode':'runtime_status','runtime_backend':'cpu'}, {'mode':'chat','prompt':'hello','runtime_backend':'cpu'}])
def test_invalid_fields_rejected(body):
    with pytest.raises(ValueError): ui.validate_job(body)


def test_unauthed_direct_execute_cannot_change_settings(fixture):
    with pytest.raises(ValueError, match='authenticated'):
        fixture.app.execute({'mode':'runtime_config','runtime_backend':'cpu'})
