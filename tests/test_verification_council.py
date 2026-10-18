import pytest

from prometheus_assistant.verification_council import evaluate_verification

COMMIT = 'a' * 40
DIGEST = 'b' * 64


def check(name='tests', status='passed', **extra):
    return dict(name=name, status=status, commit_sha=COMMIT,
                content_sha256=DIGEST, tool='pytest', tool_version='8',
                evidence_sha256='c' * 64, **extra)


def evaluate(checks=(), findings=(), required=('tests',)):
    return evaluate_verification(COMMIT, DIGEST, required, checks, findings)


def test_missing_evidence_cannot_be_replaced_by_reviewer_agreement():
    findings = [dict(source=f'model-{i}', file='a.py', line=1,
                     category='correctness', risk='low', confidence=1.0,
                     message='Looks correct', reproducible=False) for i in range(3)]
    result = evaluate(findings=findings)
    assert result['status'] == 'blocked'
    assert result['checks'][0]['status'] == 'missing'
    assert result['authorized'] is False


def test_green_is_evidence_readiness_not_approval():
    result = evaluate([check()])
    assert result['status'] == 'ready_for_owner_review'
    assert result['approval_required'] is True
    assert result['authorized'] is False


@pytest.mark.parametrize('status', ['failed', 'skipped', 'unavailable', 'running'])
def test_nonpass_check_blocks(status):
    assert evaluate([check(status=status)])['status'] == 'blocked'


def test_stale_commit_or_content_blocks():
    for field in ('commit_sha', 'content_sha256'):
        evidence = check()
        evidence[field] = 'd' * len(evidence[field])
        result = evaluate([evidence])
        assert result['checks'][0]['status'] == 'stale'


def test_duplicate_results_do_not_hide_failure():
    result = evaluate([check(), check(status='failed')])
    assert result['checks'][0]['status'] == 'conflicting'
    assert result['status'] == 'blocked'


def test_unsupported_pass_lacks_evidence():
    evidence = check()
    del evidence['evidence_sha256']
    with pytest.raises(ValueError):
        evaluate([evidence])


def test_receipt_determinism_and_binding():
    one = check()
    two = check(name='lint')
    first = evaluate([one, two], required=('lint', 'tests'))
    assert first == evaluate([two, one], required=('tests', 'lint'))
    changed = check(status='failed')
    assert evaluate([changed])['receipt_sha256'] != evaluate([one])['receipt_sha256']


def test_dedup_retains_sources_and_disagreement_as_advisory():
    base = dict(file='src/a.py', line=2, category='correctness', risk='high',
                confidence=0.8, message='Potential loss', reproducible=True)
    findings = [dict(base, source='local'), dict(base, source='remote'),
                dict(base, source='third', message='No loss', risk='low')]
    result = evaluate([check()], findings)
    assert len(result['findings']) == 2
    merged = next(f for f in result['findings'] if f['message'] == 'Potential loss')
    assert merged['sources'] == ['local', 'remote']
    assert merged['advisory'] is True
    assert result['authorized'] is False
    assert result['status'] == 'review_needed'


@pytest.mark.parametrize('required', [(), ('tests', 'tests')])
def test_empty_or_duplicate_policy_rejected(required):
    with pytest.raises(ValueError):
        evaluate(required=required)


@pytest.mark.parametrize('field,value', [('line', True), ('line', -1),
    ('confidence', float('nan')), ('file', '../secret'), ('risk', 'unknown')])
def test_malformed_findings_rejected(field, value):
    finding = dict(source='local', file='a.py', line=1, category='correctness',
                   risk='low', confidence=0.5, message='Check', reproducible=False)
    finding[field] = value
    with pytest.raises(ValueError):
        evaluate(findings=[finding])


def test_unexpected_checks_rejected_instead_of_silent_omission():
    with pytest.raises(ValueError):
        evaluate([check(name='other')])


def test_bounded_input_rejects_large_or_unending_iterables():
    import itertools
    with pytest.raises(ValueError, match='Too many'):
        evaluate(itertools.repeat(check()))


def test_reviewer_cannot_supply_approval_or_promote_claim_to_check():
    finding = dict(source='agent', file='a.py', line=1, category='correctness',
                   risk='low', confidence=1, message='All tests pass',
                   reproducible=True, authorized=True, status='passed')
    result = evaluate(findings=[finding])
    assert result['status'] == 'blocked'
    assert result['authorized'] is False
    assert result['findings'][0]['advisory'] is True


def test_uppercase_digests_normalize_to_same_receipt():
    expected = evaluate([check()])
    record = check()
    for key in ('commit_sha', 'content_sha256', 'evidence_sha256'):
        record[key] = record[key].upper()
    actual = evaluate_verification(COMMIT.upper(), DIGEST.upper(), ['tests'], [record])
    assert actual == expected


def test_receipt_binds_exact_check_tool_version():
    record = check()
    original = evaluate([record])
    record['tool_version'] = '9'
    assert original['receipt_sha256'] != evaluate([record])['receipt_sha256']
