"""Exact-byte sequential specialist coordination; all model output is fixture data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import advisory_review_runtime as runtime, project_advisory
from prometheus_assistant import project_specialists as specialists
from prometheus_assistant.advisory_review import prepare_review, qualify_review, _json_bytes
from prometheus_assistant.coding_workspace import CodingWorkspace
from prometheus_assistant.ollama import GenerationCancelled
from prometheus_assistant.project_editor import ProjectEditor
from test_specialist_resources import app_for, observation


@pytest.fixture
def team(tmp_path, monkeypatch):
    root = tmp_path/'project'
    root.mkdir()
    file = root/'app.py'
    original = b'\xef\xbb\xbfdef value():\r\n    return 1\r\n'
    file.write_bytes(original)
    workspace = CodingWorkspace(root, tmp_path/'history', resources=tmp_path/'resources')
    engine = ProjectEditor(workspace, lambda *_: dict(path='app.py', reason='Return the requested value.',
        benefit='Callers receive two.', replacements=[dict(old_text='return 1', new_text='return 2')]))
    engine.author_identity = dict(provider='ollama-local', model='fixture-author', artifact_sha256='a'*64)
    candidate = engine.propose('Return two', target='app.py')
    observed = [observation(), observation()]
    app, samples = app_for(tmp_path/'data', observed)
    app.cancel_event = threading.Event()
    progress = []
    app.progress = progress.append
    state = SimpleNamespace(app=app, engine=engine, candidate=candidate, files=project_advisory.proposed_files(engine, candidate),
        file=file, original=original, observed=observed, samples=samples, progress=progress,
        calls=[], verified=0, callback=None, verify_callback=None, rows={}, advice_status=None)
    def verify():
        state.verified += 1
        if state.verify_callback:
            state.verify_callback()
    state.verify = verify
    def review(app, candidate, files, *, author, role):
        state.calls.append((role, deepcopy(candidate), dict(files)))
        context = prepare_review(candidate, files, author=author,
            reviewer=dict(provider='ollama-local', model='fixture-reviewer', artifact_sha256='a'*64),
            **runtime.prompt_identity(candidate, files, role=role))
        report = qualify_review(candidate, files, context, json.dumps({'findings': state.rows.get(role, [])}, ensure_ascii=False),
            invocation=dict(request_sha256=context['request_sha256'], status='completed', duration_seconds=.01))
        if state.callback:
            state.callback(role)
        if state.advice_status:
            return runtime._result(*state.advice_status)
        return runtime._result('qualified_advisory', 'qualified', attempted=True, report=report)
    monkeypatch.setattr(runtime, 'run_review', review)
    return state


def run(team):
    return specialists.prepare(team.app, team.engine, team.candidate, verify=team.verify)


def claim(risk='normal', message='Check this boundary.'):
    return dict(file='app.py', line=2, category='correctness', risk=risk, confidence=.8,
        message=message, reproducible=True)


def resign(record):
    for stage in record['stages']:
        report = (stage['advice'] or {}).get('report')
        if report:
            context = report['context']
            context['request_sha256'] = hashlib.sha256(_json_bytes({k:v for k,v in context.items() if k != 'request_sha256'})).hexdigest()
            report['receipt_sha256'] = hashlib.sha256(_json_bytes({k:v for k,v in report.items() if k != 'receipt_sha256'})).hexdigest()
    record['receipt_sha256'] = specialists._digest({k:v for k,v in record.items() if k != 'receipt_sha256'})
    return record


def test_roles_review_one_immutable_snapshot_without_writes_or_authority(team):
    team.rows = {'correctness': [claim('high', 'Boundary may fail.')],
                 'test_coverage': [claim('low', 'Boundary appears covered.') ]}
    result = run(team)
    assert result['status'] == 'completed' and result['max_model_concurrency'] == 1
    assert result['authorized'] is False and result['approval_required'] is True
    assert [c[0] for c in team.calls] == ['correctness','test_coverage']
    assert team.calls[0][1:] == team.calls[1][1:]
    assert team.calls[0][2]['app.py'] == b'\xef\xbb\xbfdef value():\r\n    return 2\r\n'
    assert team.file.read_bytes() == team.original and not team.engine.workspace.history.exists()
    assert team.samples == [{'force':True}, {'force':True}]
    assert team.verified >= 4
    rows = specialists.findings(result)
    assert [r['risk'] for r in rows] == ['high','low']
    assert [r['specialist_role'] for r in rows] == ['correctness','test_coverage']
    assert all(r['advisory'] and not r['reproducible'] and r['evidence_qualification'] == 'unverified_claim' for r in rows)
    assert all(s['advice']['report']['context']['artifact_relationship'] == 'same_artifact' for s in result['stages'])
    assert specialists.needs_exact_review(result)
    assert any('Test coverage review' in msg for msg in team.progress)


@pytest.mark.parametrize('change,reason', [({'available_ram_gib':1}, 'ram_reserve'),
    ({'plugged_in':False}, 'battery_budget_unknown'), ({'cpu_percent':99}, 'cpu_busy')])
def test_hardware_changes_between_roles_retain_first_and_stop(team, change, reason):
    team.observed[1].update(change)
    result = run(team)
    assert result['status'] == 'incomplete'
    assert [s['status'] for s in result['stages']] == ['completed','deferred']
    assert len(team.calls) == 1
    assert reason in result['stages'][1]['resources']['reason_codes']
    assert specialists.needs_exact_review(result)
    assert specialists.retain(team.candidate, team.files, result) == result


def test_initial_deferral_records_both_roles_and_no_model_report(team):
    team.observed[0]['cpu_percent'] = None
    result = run(team)
    assert [s['status'] for s in result['stages']] == ['deferred','not_run']
    assert all(s['advice'] is None for s in result['stages']) and not team.calls
    assert result['stages'][1]['resources'] is None


def test_runtime_unavailable_retained_without_second_stage_or_retry(team):
    team.advice_status = ('unavailable','context_unavailable')
    result = run(team)
    assert [s['status'] for s in result['stages']] == ['unavailable','not_run']
    assert result['stages'][0]['advice']['reason_code'] == 'context_unavailable'
    assert len(team.calls) == 1 and specialists.needs_exact_review(result)


@pytest.mark.parametrize('when', ['before','during','between'])
def test_cancellation_never_runs_second_role(team, when):
    if when == 'before':
        team.app.cancel_event.set()
    elif when == 'during':
        team.callback = lambda _: team.app.cancel_event.set()
    else:
        def verify():
            if team.calls and team.verified >= 5:
                team.app.cancel_event.set()
        team.verify_callback = verify
    result = run(team)
    assert result['status'] == 'cancelled' and len(team.calls) <= 1
    assert all(c[0] == 'correctness' for c in team.calls)
    assert specialists.needs_exact_review(result)


def test_generation_cancelled_exception_is_retained_and_releases_slot(team):
    def cancel(_):
        raise GenerationCancelled('Owner stopped')
    team.callback = cancel
    result = run(team)
    assert result['status'] == 'cancelled'
    assert not specialists._MODEL_SLOT.locked()


@pytest.mark.parametrize('change', ['source','candidate','owner'])
def test_source_candidate_or_owner_change_discards_team_before_next_role(team, change):
    def mutate(_):
        if change == 'source':
            team.file.write_text('externally changed\n')
        elif change == 'candidate':
            team.candidate['goal'] = 'Changed owner goal'
        else:
            def expired():
                raise ValueError('Owner expired')
            team.verify_callback = expired
    team.callback = mutate
    with pytest.raises(ValueError):
        run(team)
    assert len(team.calls) == 1 and not specialists._MODEL_SLOT.locked()


def test_retain_and_history_validation_do_not_rerun_and_return_isolated_copies(team, monkeypatch):
    record = run(team)
    monkeypatch.setattr(runtime, 'run_review', lambda *a, **k: pytest.fail('model rerun'))
    monkeypatch.setattr(team.app.project_resources.probe, 'snapshot', lambda **k: pytest.fail('measurement rerun'))
    retained = specialists.retain(team.candidate, team.files, record)
    assert specialists.validate_saved_team(retained) == record
    retained['stages'].clear()
    assert len(record['stages']) == 2
    assert specialists.retain(team.candidate, team.files, None) is None
    assert not specialists.needs_exact_review(record)


@pytest.mark.parametrize('index,prior', [(0, 'local-advisory-v3'), (1, 'local-advisory-test-coverage-v1')])
def test_prior_role_prompt_is_preserved_for_history_but_requires_fresh_exact_review(team, monkeypatch, index, prior):
    record = run(team)
    record['stages'][index]['advice']['report']['context']['prompt_version'] = prior
    # A saved historical prompt hash is retained as historical provenance. It
    # cannot establish the current request's exact identity during apply.
    record['stages'][index]['advice']['report']['context']['prompt_sha256'] = 'd'*64
    resign(record)
    monkeypatch.setattr(runtime, 'run_review', lambda *a, **k: pytest.fail('history must not rerun model'))
    monkeypatch.setattr(team.app.project_resources.probe, 'snapshot', lambda **k: pytest.fail('history must not resample'))
    assert specialists.validate_saved_team(record) == record
    assert record['authorized'] is False and record['approval_required'] is True
    with pytest.raises(ValueError, match='prompt'):
        specialists.retain(team.candidate, team.files, record)


@pytest.mark.parametrize('index,wrong', [(0, 'local-advisory-test-coverage-v1'),
    (1, 'local-advisory-v3'), (0, 'local-advisory-v2'), (1, 'local-advisory-test-coverage-v0')])
def test_history_accepts_only_known_versions_for_the_exact_role(team, index, wrong):
    record = run(team)
    record['stages'][index]['advice']['report']['context']['prompt_version'] = wrong
    resign(record)
    with pytest.raises(ValueError, match='role and snapshot'):
        specialists.validate_saved_team(record)


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(authorized=True), lambda r: r.update(schema_version=True),
    lambda r: r.update(status=[]), lambda r: r['stages'][0].update(status=[]),
    lambda r: r.update(max_model_concurrency=2), lambda r: r.update(status='incomplete'),
    lambda r: r['stages'].reverse(), lambda r: r['stages'].pop(),
    lambda r: r['stages'][0].update(status='not_run'),
    lambda r: r['stages'][0].update(resources=None),
    lambda r: r['stages'][0]['advice']['report']['context'].update(prompt_version='wrong'),
    lambda r: r['stages'][0]['advice']['report']['context'].update(candidate_sha256='e'*64),
    lambda r: r['stages'][0]['advice']['report'].update(schema_version=True),
    lambda r: r['stages'][0]['advice']['report']['context'].update(schema_version=True),
    lambda r: r['stages'][0]['resources'].update(decision='defer'),
    lambda r: r['files'][0].update(lines=True),
])
def test_saved_team_rejects_structural_tampering_even_with_new_receipt(team, mutate):
    record = run(team)
    mutate(record)
    resign(record)
    with pytest.raises(ValueError):
        specialists.validate_saved_team(record)


def test_exact_retention_rejects_role_prompt_hash_tamper_and_changed_incomplete_candidate(team):
    record = run(team)
    record['stages'][1]['advice']['report']['context']['prompt_sha256'] = 'c'*64
    resign(record)
    with pytest.raises(ValueError, match='prompt'):
        specialists.retain(team.candidate, team.files, record)
    team.observed[0]['cpu_percent'] = None
    # The observation fixture advances per sample; reset its contents for both slots.
    team.observed[1]['cpu_percent'] = None
    incomplete = run(team)
    changed = dict(team.candidate, goal='Another goal')
    with pytest.raises(ValueError, match='different candidate'):
        specialists.retain(changed, team.files, incomplete)


def test_oversize_malformed_and_corrupted_team_rejected(team):
    record = run(team)
    for mutate in (lambda r:r.update(extra='x'),
                   lambda r:r.update(receipt_sha256='f'*64),
                   lambda r:r['stages'][0]['advice'].update(reason='x'*110001)):
        changed = deepcopy(record)
        mutate(changed)
        with pytest.raises(ValueError):
            specialists.retain(team.candidate, team.files, changed)


def test_single_model_slot_defers_overlapping_coordinator(team):
    entered, release = threading.Event(), threading.Event()
    def wait(_):
        entered.set()
        assert release.wait(3)
    team.callback = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, team)
        assert entered.wait(3)
        try:
            second = run(team)
            assert second['stages'][0]['status'] == 'deferred'
            assert 'model_slot_busy' in second['stages'][0]['resources']['reason_codes']
        finally:
            release.set()
        assert first.result(timeout=3)['status'] == 'completed'
    assert len(team.calls) == 2


def test_large_qualified_outputs_keep_bounded_incomplete_evidence_without_truncation(team):
    # Each individual response qualifies; the combined history must stay bounded.
    rows = [claim(message=str(i)+' '+('x'*3600)) for i in range(16)]
    team.rows = dict(correctness=rows, test_coverage=rows)
    result = run(team)
    assert result['status'] == 'incomplete'
    assert [s['status'] for s in result['stages']] == ['completed','unavailable']
    assert result['stages'][1]['advice']['reason_code'] == 'evidence_limit'
    assert len(result['stages'][0]['advice']['report']['findings']) == 16
    assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) <= 110000


def test_large_valid_non_ascii_evidence_uses_utf8_storage_bound(team):
    rows = [claim(message=str(i)+' '+('\u754c'*1800)) for i in range(8)]
    team.rows = dict(correctness=rows, test_coverage=rows)
    result = run(team)
    assert result['status'] == 'completed'
    assert 80000 < len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) <= 110000
    assert len(_json_bytes(result)) > 110000
    assert specialists.validate_saved_team(result) == result
    assert specialists.retain(team.candidate, team.files, result) == result


@pytest.mark.parametrize('mode',['Idle','Battery Saver','Critical Recovery'])
def test_mode_defers_without_any_inference_and_releases_slot(team,mode):
    team.app.project_resources.set_mode_preference(mode)
    result=run(team)
    assert not team.calls and result['status']=='incomplete'
    assert result['stages'][0]['status']=='deferred'
    assert specialists._MODEL_SLOT.acquire(blocking=False)
    specialists._MODEL_SLOT.release()


def test_mode_change_between_roles_prevents_second_inference(team):
    team.callback=lambda role:team.app.project_resources.set_mode_preference('Idle')
    result=run(team)
    assert len(team.calls)==1
    assert [s['status'] for s in result['stages']]==['completed','deferred']
    assert result['status']=='incomplete'
    assert 'mode_idle' in result['stages'][1]['resources']['reason_codes']
