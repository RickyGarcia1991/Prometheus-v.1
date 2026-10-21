"""Exercise owner feedback through the actual single-worker application dispatcher."""
import copy
import json

import pytest

from test_scheduled_syntax_ui import fixture as worker_fixture
from prometheus_assistant import integration_ui, runtime_settings_ui, project_tasks_ui, ui_server
from prometheus_assistant.runtime_preferences import Preferences
from prometheus_assistant.scoped_grants import GrantStore
from prometheus_assistant.memory import MemoryStore

KINDS = ('runtime_config', 'idle_config', 'integration', 'integration_access', 'remote_syntax', 'scheduled_syntax')


@pytest.fixture
def setup(worker_fixture, monkeypatch, tmp_path):
    f = worker_fixture
    f.app._runtime_preferences = Preferences(tmp_path/'preferences.sqlite3')
    f.app.grants = GrantStore(tmp_path/'authority/grants.sqlite3', host=lambda: 'fixture-host')
    monkeypatch.setattr(integration_ui, '_call', lambda *a, **k: pytest.fail('Feedback must not call an adapter'))
    f.saved = []
    original = f.app.save
    def save(request, data, *args, **kwargs):
        f.saved.append(copy.deepcopy((request, data)))
        return original(request, data, *args, **kwargs)
    monkeypatch.setattr(f.app, 'save', save)
    return f


def body(f, kind):
    if kind == 'runtime_config':
        return dict(mode=kind, runtime_backend='cpu')
    if kind == 'idle_config':
        return dict(mode=kind, enabled=True, prompt='Python syntax research', interval_minutes=60)
    if kind == 'integration':
        return dict(mode=kind, adapter='agentmemory', operation='recall', arguments=dict(project='home', query='notes'))
    if kind == 'integration_access':
        return dict(mode=kind, task=integration_ui.access_from_chat('Give AgentMemory full access to project home for 30 minutes'))
    return dict(mode=kind+'_prepare', project=str(f.project), remote_files='hello.py')


def changes(f, parent, **updates):
    return dict(mode='approval_decision', approval_id=parent['id'],
        approval_fingerprint=parent['fingerprint'], approval_decision='request_changes',
        approval_feedback='Use the corrected inputs before preparing another review.', session=parent['session'], **updates)


@pytest.mark.parametrize('kind', KINDS)
def test_changes_close_without_effect_then_link_fresh_review_and_saved_history(setup, kind):
    f = setup
    request = body(f, kind)
    parent = f.run(request)['details']['approval']
    old_job = parent['payload'].get('request', {}).get('job_id')
    result = f.run(changes(f, parent))
    context = result['details']['capability_revision']
    assert context['kind'] == kind and context['origin_approval'] == parent['id']
    assert f.app.action_approvals.pending(owner=f.owner, binding=f.binding) is None
    assert not f.calls and f.app._runtime_preferences.snapshot()['backend'] == 'automatic'
    assert not f.app.grants.inspect_tasks(owner=f.owner)
    assert not project_tasks_ui.research_store(f.app).status()['settings']['enabled']
    assert not f.store().list()
    (f.project/'hello.py').write_bytes(b'new_value = 73\n')
    request.update(session=parent['session'], capability_revision_token=context['capability_revision_token'])
    child = f.run(request)['details']['approval']
    assert child['id'] != parent['id'] and child['origin_approval'] == parent['id']
    if old_job:
        assert child['payload']['request']['job_id'] != old_job
        assert child['payload']['request']['files'][0]['content'] == 'new_value = 73\n'
    assert 'capability_revision_token' not in json.dumps(child)
    assert context['capability_revision_token'] not in json.dumps(f.saved)
    assert not f.calls and not f.store().list()
    f.run(dict(mode='approval_decision', approval_id=child['id'], approval_fingerprint=child['fingerprint'],
               approval_decision='reject', session=child['session']))
    rows = f.app.action_approvals.history(owner=f.owner)
    recorded = next(row for row in rows if row['id'] == parent['id'])
    assert recorded['decision'] == 'request_changes'
    assert recorded['capability_revisions'][0]['child_id'] == child['id']
    assert recorded['capability_revisions'][0]['child_state'] == 'recorded'
    with MemoryStore(f.memory) as memory:
        assert context['capability_revision_token'] not in json.dumps(memory.history(parent['session'], include_metadata=True))
    repeated = f.submit(request)
    assert repeated['state'] == 'error' and not f.calls and not f.store().list()


@pytest.mark.parametrize('mutation', ('owner', 'binding', 'session', 'fingerprint', 'cancel'))
def test_bad_changes_cannot_consume_or_execute(setup, mutation):
    f = setup
    parent = f.run(body(f, 'remote_syntax'))['details']['approval']
    request = changes(f, parent)
    if mutation == 'owner': f.owner = 'f'*64
    if mutation == 'binding': f.binding = '8'*64
    if mutation == 'session': request['session'] = '7'*32
    if mutation == 'fingerprint': request['approval_fingerprint'] = '9'*64
    if mutation == 'cancel':
        # submit clears stop state; cancellation at the central route must still gate effects.
        f.app.cancel_event.set()
        from prometheus_assistant.capability_revision_ui import execute
        with pytest.raises(ui_server.GenerationCancelled):
            execute(f.app, ui_server.validate_job(request), grant_owner=f.grant, binding=f.binding)
    else:
        assert f.submit(request)['state'] == 'error'
    assert not f.calls and not f.store().list()


@pytest.mark.parametrize('kind', KINDS)
def test_restart_does_not_restore_revision_authority(setup, kind):
    f = setup
    request = body(f, kind)
    parent = f.run(request)['details']['approval']
    token = f.run(changes(f, parent))['details']['capability_revision']['capability_revision_token']
    f.restart()
    failed = f.submit({**request, 'session':parent['session'], 'capability_revision_token':token})
    assert failed['state'] == 'error'
    assert not f.calls and not f.store().list()
    history = f.app.action_approvals.history(owner=f.owner)
    assert history[0]['id'] == parent['id'] and history[0]['decision'] == 'request_changes'


def test_revision_cannot_use_existing_integration_grant(setup):
    f = setup
    request = body(f, 'integration')
    parent = f.run(request)['details']['approval']
    token = f.run(changes(f, parent))['details']['capability_revision']['capability_revision_token']
    task = integration_ui.access_from_chat('Give AgentMemory full access to project home for 30 minutes')
    grant = f.app.grants.issue_task(task, owner=f.owner)
    f.app.integration_tasks[(f.owner, f.binding, parent['session'])] = grant['id']
    with pytest.raises(ValueError):
        ui_server.validate_job({**request, 'session':parent['session'], 'task_grant':grant['id'],
                                'capability_revision_token':token})
    with pytest.raises(ValueError):
        ui_server.validate_job(dict(mode='chat', prompt='Recall from project home: notes',
            session=parent['session'], capability_revision_token=token))
    replacement = f.run({**request, 'session':parent['session'], 'capability_revision_token':token})
    assert replacement['details']['approval']['origin_approval'] == parent['id']


def test_wrong_worker_project_fails_before_probing_or_reading_source(setup, monkeypatch, tmp_path):
    f = setup
    request = body(f, 'remote_syntax')
    parent = f.run(request)['details']['approval']
    token = f.run(changes(f, parent))['details']['capability_revision']['capability_revision_token']
    other = tmp_path/'other'; other.mkdir(); (other/'hello.py').write_text('a = 1\n')
    monkeypatch.setattr(f.app._remote_worker_client, 'target', lambda: pytest.fail('Wrong scope reached target probe'))
    result = f.submit({**request, 'project':str(other), 'session':parent['session'], 'capability_revision_token':token})
    assert result['state'] == 'error' and not f.calls


@pytest.mark.parametrize('mode', ('runtime_status', 'idle_run', 'remote_syntax_recover', 'scheduled_syntax_tick',
                               'scheduled_syntax_recover', 'scheduled_syntax_cancel', 'approval_decision', 'project_edit'))
def test_token_is_never_valid_for_effect_or_recovery_modes(mode):
    with pytest.raises(ValueError):
        ui_server.validate_job(dict(mode=mode, prompt='test', session='c'*32, capability_revision_token='d'*32))


def test_idle_revision_cannot_silently_take_disable_fast_path(setup):
    f = setup
    parent = f.run(body(f, 'idle_config'))['details']['approval']
    token = f.run(changes(f, parent))['details']['capability_revision']['capability_revision_token']
    with pytest.raises(ValueError):
        ui_server.validate_job(dict(mode='idle_config', enabled=False, session=parent['session'], capability_revision_token=token))
