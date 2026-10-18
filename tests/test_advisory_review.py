"""Contract checks for advisory provenance, not model correctness or attestation."""
from copy import deepcopy
import hashlib
import json

import pytest

from prometheus_assistant.advisory_review import (
    MAX_CANDIDATE_BYTES, MAX_RESPONSE_BYTES, MAX_SNAPSHOT_BYTES,
    prepare_review, qualify_review, validate_saved_advice,
)


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case():
    source = b'value = 1\nprint(value)\n'
    return {
        'candidate': {'version': 1, 'path': 'src/a.py', 'content': source.decode(),
                      'after_sha256': sha(source), 'reason': 'Owner requested this edit.'},
        'files': {'src/a.py': source},
        'author': {'provider': 'local', 'model': 'writer', 'artifact_sha256': 'a' * 64},
        'reviewer': {'provider': 'local', 'model': 'checker', 'artifact_sha256': 'b' * 64},
    }


def prepare(case, **changes):
    options = dict(author=case['author'], reviewer=case['reviewer'],
                   prompt_version='advisory-v1', prompt_sha256='c' * 64)
    options.update(changes)
    return prepare_review(case['candidate'], case['files'], **options)


def finding(**changes):
    row = dict(file='src/a.py', line=2, category='correctness', risk='high',
               confidence=0.8, message='Check this boundary.', reproducible=True)
    row.update(changes)
    return row


def saved_advice(case):
    return dict(status='qualified_advisory', reason_code='qualified', reason='Retained advice',
                attempted=True, authorized=False, report=qualify(case))


def test_saved_advice_survives_fresh_memory_reader_without_source_or_authority(case, tmp_path):
    from prometheus_assistant.memory import MemoryStore
    advice = saved_advice(case)
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        session = memory.create_session('fixture')
        memory.save_exchange(session, 'Edit', 'Prepared', details={'task_review': {
            'advisory_review': advice, 'candidate': case['candidate'], 'approval_id': 'not-retained'},
            'approval': {'id': 'not-retained'}, 'token': 'never-retained'})
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        details = memory.history(session, include_metadata=True)[-1]['details']
    assert details == {'task_review': {'advisory_review': advice}}


@pytest.mark.parametrize('change', [
    lambda r: r.update(authorized=True),
    lambda r: r.update(checks=[{'status':'passed'}]),
    lambda r: r.update(secret='not-allowed'),
    lambda r: r['findings'][0].update(reproducible=True),
    lambda r: r['findings'][0].update(line=9999),
    lambda r: r['context'].update(artifact_relationship='same_artifact'),
    lambda r: r['context'].update(request_sha256='0'*64),
    lambda r: r['invocation'].update(status='cancelled'),
    lambda r: r['context']['files'][0].update(bytes=64001),
])
def test_rehashed_saved_report_still_cannot_claim_authority_or_bad_provenance(case, change):
    value = saved_advice(case)
    report = value['report']
    change(report)
    report.pop('receipt_sha256')
    report['receipt_sha256'] = sha(json.dumps(report, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=True).encode())
    with pytest.raises(ValueError):
        validate_saved_advice(value)


def test_saved_advice_detects_unhashed_tampering(case):
    advice = saved_advice(case)
    advice['report']['findings'][0]['message'] = 'changed'
    with pytest.raises(ValueError, match='receipt'):
        validate_saved_advice(advice)


def test_large_unicode_advice_uses_utf8_storage_bound(case, tmp_path):
    from prometheus_assistant.memory import MemoryStore
    advice = saved_advice(case)
    advice['report'] = qualify(case, json.dumps({'findings': [finding(message=str(i)+'é'*3998) for i in range(7)]}, ensure_ascii=False))
    assert len(json.dumps(advice, ensure_ascii=True)) > 120000
    assert validate_saved_advice(advice) == advice
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        session = memory.create_session('fixture')
        memory.save_exchange(session, 'Edit', 'Saved', details={'project_edit': {'advisory_review': advice}})
        assert memory.history(session, include_metadata=True)[-1]['details']['project_edit']['advisory_review'] == advice


def test_corrupt_huge_duration_does_not_break_history(case, tmp_path):
    from prometheus_assistant.memory import MemoryStore
    advice = saved_advice(case)
    report = advice['report']
    report['invocation']['duration_seconds'] = 10**1000
    report.pop('receipt_sha256')
    report['receipt_sha256'] = sha(json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode())
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        session = memory.create_session('fixture')
        memory.save_exchange(session, 'Edit', 'Original text', details={'model': 'fixture'})
        memory.db.execute('UPDATE turn_metadata SET details_json=?', (json.dumps({'project_edit': {'advisory_review': advice}}),))
        memory.db.commit()
        saved = memory.history(session, include_metadata=True)[-1]
        assert saved['content'] == 'Original text'
        assert 'advisory_review' not in str(saved.get('details', {}))


def qualify(case, response=None, *, prepared=None, invocation=None):
    prepared = prepared or prepare(case)
    invocation = invocation or dict(request_sha256=prepared['request_sha256'],
                                     status='completed', duration_seconds=0.25)
    if response is None and invocation['status'] == 'completed':
        response = json.dumps({'findings': [finding()]})
    return qualify_review(case['candidate'], case['files'], prepared, response,
                          invocation=invocation)


def test_qualified_claim_has_exact_provenance_but_never_grants_approval(case):
    prepared = prepare(case)
    result = qualify(case, prepared=prepared)
    assert result['status'] == 'qualified_advisory'
    assert result['authorized'] is False
    assert result['approval_required'] is True
    assert result['context'] == prepared
    assert result['context']['artifact_relationship'] == 'different_artifact'
    assert result['findings'][0]['reproduction_claimed'] is True
    assert result['findings'][0]['reproducible'] is False
    assert result['findings'][0]['evidence_qualification'] == 'unverified_claim'
    assert result['findings'][0]['advisory'] is True


def test_receipts_bind_prompt_output_and_invocation_and_are_deterministic(case):
    original = qualify(case)
    assert original == qualify(case)
    changed = qualify(case, json.dumps({'findings': [finding(message='Different claim.')]}))
    assert original['receipt_sha256'] != changed['receipt_sha256']
    assert prepare(case, prompt_version='v2')['request_sha256'] != prepare(case)['request_sha256']
    assert prepare(case, prompt_sha256='d' * 64)['request_sha256'] != prepare(case)['request_sha256']


@pytest.mark.parametrize('change', ['candidate', 'bytes', 'candidate_and_bytes', 'prepared', 'invocation'])
def test_stale_or_tampered_binding_rejected(case, change):
    before = prepare(case)
    invocation = dict(request_sha256=before['request_sha256'], status='completed', duration_seconds=1)
    if change == 'candidate':
        case['candidate']['reason'] = 'A different request.'
    elif change in ('bytes', 'candidate_and_bytes'):
        case['files']['src/a.py'] = b'value = 2\n'
        if change == 'candidate_and_bytes':
            case['candidate'].update(content='value = 2\n', after_sha256=sha(b'value = 2\n'))
    elif change == 'prepared':
        before['reviewer']['model'] = 'forged'
    else:
        invocation['request_sha256'] = 'e' * 64
    with pytest.raises(ValueError):
        qualify(case, prepared=before, invocation=invocation)


@pytest.mark.parametrize('alias', ['writer', 'different-tag'])
def test_same_artifact_alias_is_not_an_independent_reviewer(case, alias):
    case['reviewer'].update(provider='another-provider', model=alias, artifact_sha256='A' * 64)
    assert prepare(case)['artifact_relationship'] == 'same_artifact'


def test_unknown_artifact_does_not_imply_independence(case):
    case['reviewer']['artifact_sha256'] = None
    assert prepare(case)['artifact_relationship'] == 'unestablished'
    assert prepare(case, author=None)['artifact_relationship'] == 'unestablished'


@pytest.mark.parametrize('field,value', [
    ('file', '../a.py'), ('file', '/src/a.py'), ('file', 'C:/src/a.py'),
    ('file', 'src\\a.py'), ('file', 'src//a.py'), ('file', './src/a.py'),
    ('file', 'other.py'), ('file', 'SRC/a.py'), ('line', 3), ('line', 0),
    ('line', True), ('confidence', float('nan')), ('confidence', 2),
    ('confidence', 10 ** 1000),
    ('risk', 'approved'), ('reproducible', 'yes'), ('message', ''),
])
def test_findings_must_use_real_reviewed_paths_lines_and_strict_fields(case, field, value):
    with pytest.raises(ValueError):
        qualify(case, json.dumps({'findings': [finding(**{field: value})]}))


@pytest.mark.parametrize('field,value', [
    ('authorized', True), ('source', 'trusted checker'), ('provider', 'local'),
    ('evidence_sha256', 'd' * 64), ('status', 'passed'),
])
def test_model_cannot_supply_trusted_fields(case, field, value):
    for response in ({'findings': [], field: value}, {'findings': [finding(**{field: value})]}):
        with pytest.raises(ValueError):
            qualify(case, json.dumps(response))


@pytest.mark.parametrize('response', [
    '{"findings":[],"findings":[]}', '{"findings":[{"file":"a","file":"b"}]}',
    '{"findings":NaN}', '[]', '{}', 'null', 'not json', '{"findings":{}}',
    '{"findings":' + '[' * 1500 + '0' + ']' * 1500 + '}',
])
def test_malformed_duplicate_or_deep_json_rejected(case, response):
    with pytest.raises(ValueError):
        qualify(case, response)


def test_bounds_and_nontext_response(case):
    for response in ('x' * (MAX_RESPONSE_BYTES + 1), {'findings': []},
                     json.dumps({'findings': [finding()] * 33})):
        with pytest.raises(ValueError):
            qualify(case, response)
    case['files']['src/a.py'] = b'x' * (MAX_SNAPSHOT_BYTES + 1)
    case['candidate']['after_sha256'] = sha(case['files']['src/a.py'])
    with pytest.raises(ValueError):
        prepare(case)
    case['candidate']['reason'] = 'x' * (MAX_CANDIDATE_BYTES + 1)
    with pytest.raises(ValueError):
        prepare(case)


@pytest.mark.parametrize('state', ['timed_out', 'cancelled', 'unavailable'])
def test_incomplete_invocation_is_explicit_and_cannot_accept_model_output(case, state):
    before = prepare(case)
    invocation = dict(request_sha256=before['request_sha256'], status=state, duration_seconds=1)
    result = qualify_review(case['candidate'], case['files'], before, None, invocation=invocation)
    assert result['status'] == state
    assert result['findings'] == []
    assert result['authorized'] is False
    with pytest.raises(ValueError):
        qualify(case, '{"findings":[]}', prepared=before, invocation=invocation)


def test_duplicate_claims_do_not_create_extra_votes_and_disagreement_is_preserved(case):
    result = qualify(case, json.dumps({'findings': [finding(), finding(), finding(risk='low')]}))
    assert len(result['findings']) == 2
    assert result['duplicate_findings'] == 1
    assert {row['risk'] for row in result['findings']} == {'low', 'high'}
    assert all(row['reproducible'] is False for row in result['findings'])


def test_empty_result_is_advisory_not_a_deterministic_pass(case):
    from prometheus_assistant.verification_council import evaluate_verification

    result = qualify(case, '{"findings":[]}')
    assert result['status'] == 'qualified_advisory'
    assert result['findings'] == []
    assert result['checks'] == []
    assert result['authorized'] is False
    council = evaluate_verification(None, result['context']['candidate_sha256'],
                                    ['tests'], result['checks'], result['findings'])
    assert council['status'] == 'blocked'


def test_council_retains_qualified_findings_as_unreproduced_advice(case):
    from prometheus_assistant.verification_council import evaluate_verification
    from prometheus_assistant.project_task_evidence import candidate_digest

    result = qualify(case)
    digest = candidate_digest(case['candidate'])
    assert result['context']['candidate_sha256'] == digest
    council = evaluate_verification(None, digest, ['tests'], [], result['findings'])
    assert council['status'] == 'blocked'
    assert council['findings'][0]['reproducible'] is False
    assert council['authorized'] is False


def test_limits_use_utf8_bytes_and_empty_files_have_no_finding_location(case):
    response = json.dumps({'findings': [finding(message='\u00e9' * 4096)]}, ensure_ascii=False)
    assert qualify(case, response)['status'] == 'qualified_advisory'
    with pytest.raises(ValueError):
        qualify(case, '\u00e9' * (MAX_RESPONSE_BYTES // 2 + 1))
    case['files']['src/a.py'] = b''
    case['candidate']['after_sha256'] = sha(b'')
    assert qualify(case, '{"findings":[]}')['context']['files'][0]['lines'] == 0
    with pytest.raises(ValueError):
        qualify(case, json.dumps({'findings': [finding(line=1)]}))


def test_preparation_does_not_mutate_inputs_and_results_do_not_alias_them(case):
    before = deepcopy(case)
    result = qualify(case)
    assert case == before
    result['context']['reviewer']['model'] = 'modified result'
    assert case == before


def test_multifile_snapshot_bom_crlf_and_nonexecution(case, tmp_path):
    marker = tmp_path / 'must-not-execute'
    body = ('from pathlib import Path\r\nPath(' + repr(str(marker)) + ').touch()\r\n').encode()
    body = b'\xef\xbb\xbf' + body
    second = {'version': 1, 'path': 'b.py', 'after_sha256': sha(body)}
    case['candidate'] = {'version': 2, 'candidates': [case['candidate'], second]}
    case['files']['b.py'] = body
    result = qualify(case, json.dumps({'findings': [finding(file='b.py', line=2)]}))
    assert result['findings'][0]['line'] == 2
    assert not marker.exists()
    assert {row['path'] for row in result['context']['files']} == {'src/a.py', 'b.py'}


def test_candidate_and_snapshot_require_exact_file_set_and_unique_paths(case):
    original = deepcopy(case)
    case['files']['extra.py'] = b'pass\n'
    with pytest.raises(ValueError):
        prepare(case)
    case = deepcopy(original)
    case['candidate'] = {'version': 2, 'candidates': [case['candidate'], deepcopy(case['candidate'])]}
    with pytest.raises(ValueError):
        prepare(case)


@pytest.mark.parametrize('field,value', [('duration_seconds', -1), ('duration_seconds', True),
    ('duration_seconds', float('inf')), ('duration_seconds', 10 ** 1000),
    ('status', 'passed'), ('provider', 'spoofed')])
def test_invocation_is_strict(case, field, value):
    before = prepare(case)
    invocation = dict(request_sha256=before['request_sha256'], status='completed', duration_seconds=1)
    invocation[field] = value
    with pytest.raises(ValueError):
        qualify(case, prepared=before, invocation=invocation)
