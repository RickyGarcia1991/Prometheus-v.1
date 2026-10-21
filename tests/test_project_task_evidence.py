import pytest

from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.project_editor import ProjectEditor
from prometheus_assistant.project_change_set import propose_group, apply_group
from prometheus_assistant.project_task_evidence import prepare_evidence, finish_evidence
from prometheus_assistant.verification_council import evaluate_verification


@pytest.fixture
def engine(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    for name in ('a.py', 'b.py'):
        (root / name).write_text('value = 1\n', encoding='utf-8')
    workspace = CodingWorkspace(root, tmp_path / 'history', resources=tmp_path / 'resources')
    def generate(messages, schema):
        import json
        data = json.loads(messages[-1]['content'])
        # Target path is supplied in the actual model request; no output test claims.
        return {'path': data['path'], 'reason': 'Owner requested value two.',
                'benefit': 'Uses requested value.',
                'replacements': [{'old_text': 'value = 1', 'new_text': 'value = 2'}]}
    return ProjectEditor(workspace, generate)


def proposal(engine, path='a.py'):
    return engine.propose('Set value to two', target=path)


def test_preflight_checks_real_workspace_but_tests_are_pending(engine):
    candidate = proposal(engine)
    report = prepare_evidence(engine, candidate)
    assert report['status'] == 'pending_checks'
    assert report['checks'] == [{'preset': 'python-compile', 'target': 'a.py', 'status': 'pending'}]
    assert report['authorized'] is False
    assert (engine.workspace.root / 'a.py').read_text() == 'value = 1\n'


def test_stale_candidate_rejected_before_review(engine):
    candidate = proposal(engine)
    (engine.workspace.root / 'a.py').write_text('value = 3\n')
    with pytest.raises(ValueError):
        prepare_evidence(engine, candidate)


def test_real_single_apply_evidence(engine):
    candidate = proposal(engine)
    before = prepare_evidence(engine, candidate)
    result = engine.apply(candidate)
    report = finish_evidence(engine, candidate, before, result)
    assert report['status'] == 'passed'
    assert report['council']['commit_sha'] is None
    assert report['council']['identity_kind'] == 'content_snapshot'
    assert report['authorized'] is False


def test_real_group_apply_evidence(engine):
    candidate = propose_group(engine, 'Set values to two', ['a.py', 'b.py'])
    before = prepare_evidence(engine, candidate)
    result = apply_group(engine, candidate)
    report = finish_evidence(engine, candidate, before, result)
    assert report['status'] == 'passed'
    assert len(report['checks']) == 2


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'failed', 'fake', 'unexpected', 'timeout', 'zero'])
def test_incomplete_or_forged_checks_never_green(engine, change):
    candidate = proposal(engine)
    before = prepare_evidence(engine, candidate)
    result = engine.apply(candidate)
    if change == 'missing':
        result['checks'] = []
    elif change == 'duplicate':
        result['checks'] *= 2
    elif change == 'failed':
        result['checks'][0]['exit_code'] = 1
    elif change == 'fake':
        result['checks'] = [{'preset': 'python-compile', 'target': 'a.py', 'passed': True}]
    elif change == 'unexpected':
        result['checks'].append({'preset': 'other', 'target': 'a.py', 'passed': True})
    elif change == 'timeout':
        result['checks'][0]['timed_out'] = True
    elif change == 'zero':
        result['checks'][0]['output'] = 'Ran 0 tests in 0.01s'
    assert finish_evidence(engine, candidate, before, result)['status'] == 'blocked'


def test_newer_edit_invalidates_applied_snapshot(engine):
    candidate = proposal(engine)
    before = prepare_evidence(engine, candidate)
    result = engine.apply(candidate)
    (engine.workspace.root / 'a.py').write_text('value = 99\n')
    report = finish_evidence(engine, candidate, before, result)
    assert report['status'] == 'blocked'
    assert report['snapshot'] == 'failed'


def test_candidate_mutation_invalidates_prepared_evidence(engine):
    candidate = proposal(engine)
    before = prepare_evidence(engine, candidate)
    result = engine.apply(candidate)
    candidate['reason'] = 'Changed review text'
    with pytest.raises(ValueError, match='exact candidate'):
        finish_evidence(engine, candidate, before, result)


def test_tampered_prepared_record_rejected(engine):
    candidate = proposal(engine)
    before = prepare_evidence(engine, candidate)
    result = engine.apply(candidate)
    before['checks'][0]['status'] = 'passed'
    with pytest.raises(ValueError):
        finish_evidence(engine, candidate, before, result)


def test_snapshot_identity_does_not_accept_unrelated_git_evidence():
    record = dict(name='test', status='passed', commit_sha='a'*40,
                  content_sha256='b'*64, tool='runner', tool_version='1', evidence_sha256='c'*64)
    report = evaluate_verification(None, 'b'*64, ['test'], [record])
    assert report['checks'][0]['status'] == 'stale'




def test_actual_syntax_failure_and_rollback_is_blocked(engine):
    engine.generate = lambda *_: {'path': 'a.py', 'reason': 'Change requested.',
        'benefit': 'Exercise syntax failure.',
        'replacements': [{'old_text': 'value = 1', 'new_text': 'value = ('}]}
    candidate = proposal(engine)
    prepared = prepare_evidence(engine, candidate)
    result = engine.apply(candidate)
    assert result['status'] == 'failed'
    assert result['rollback']['undone'] is True
    assert finish_evidence(engine, candidate, prepared, result)['status'] == 'blocked'


def test_no_tests_is_explicitly_unavailable(engine):
    (engine.workspace.root / 'note.txt').write_text('value = 1\n')
    candidate = proposal(engine, 'note.txt')
    prepared = prepare_evidence(engine, candidate)
    assert prepared['test_coverage'] == 'unavailable'
    result = engine.apply(candidate)
    report = finish_evidence(engine, candidate, prepared, result)
    assert report['status'] == 'blocked'
    assert report['test_coverage'] == 'unavailable'
    assert report['snapshot'] == 'passed'


def test_model_claim_cannot_replace_real_preflight(engine):
    candidate = proposal(engine)
    candidate['checks'][0]['passed'] = True
    with pytest.raises(ValueError):
        prepare_evidence(engine, candidate)
