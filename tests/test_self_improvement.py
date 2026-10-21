"""Synthetic signed-payload fixtures; no real signer, activation or model used."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

import pytest

from prometheus_assistant.ollama import GenerationCancelled
from prometheus_assistant.self_improvement import SelfImprovementEngine, fingerprint


TARGET = 'src/prometheus_assistant/calculator.py'
CHECKS = ('python-regression', 'javascript-regression')
SOURCE = b'def add(a, b):\n    return a - b\n'
FIXED = b'def add(a, b):\n    return a + b\n'


def sha(body):
    return hashlib.sha256(body).hexdigest()


def checks():
    return {'checks': [{'id': name, 'executed': True, 'exit_code': 0,
                        'tests_run': 1, 'log_sha256': sha(b'one test passed')}
                       for name in CHECKS]}


def patch():
    return {'path': TARGET, 'old_text': 'return a - b',
            'new_text': 'return a + b', 'reason': 'Addition should sum both arguments.'}


def manifest(root):
    files = sorted(path for path in root.rglob('*') if path.is_file()
                   and path.name not in {'SHA256-MANIFEST.json', 'SHA256-MANIFEST.sig'})
    (root / 'SHA256-MANIFEST.json').write_text(json.dumps([
        {'path': path.relative_to(root).as_posix(), 'sha256': sha(path.read_bytes()),
         'size_bytes': path.stat().st_size} for path in files]), encoding='utf-8')


@pytest.fixture
def app(tmp_path):
    baseline = tmp_path / 'signed-baseline'
    (baseline / TARGET).parent.mkdir(parents=True)
    (baseline / TARGET).write_bytes(SOURCE)
    (baseline / 'tests').mkdir()
    (baseline / 'tests/test_baseline.py').write_text(
        'from pathlib import Path\n'
        'def test_addition():\n'
        '    scope = {}\n'
        '    source = Path(__file__).parents[1] / "src/prometheus_assistant/calculator.py"\n'
        '    exec(compile(source.read_text(), str(source), "exec"), scope)\n'
        '    assert scope["add"](9, 3) == 12\n', encoding='utf-8')
    (baseline / '.env.example').write_text('EXAMPLE_ONLY=1\n', encoding='utf-8')
    manifest(baseline)
    (baseline / 'SHA256-MANIFEST.sig').write_bytes(b'synthetic fixture signature')
    calls = {'generate': [], 'run': [], 'promote': []}

    def generate(messages, format):
        calls['generate'].append((messages, format))
        return patch()

    def runner(candidate, active, required, *, cancel_event=None):
        calls['run'].append((candidate, active, required))
        assert (active / TARGET).read_bytes() == SOURCE
        assert (candidate / TARGET).read_bytes() == FIXED
        assert not (candidate / 'SHA256-MANIFEST.sig').exists()
        assert not (candidate / 'SHA256-MANIFEST.json').exists()
        assert (candidate / 'tests/test_baseline.py').read_bytes() == (active / 'tests/test_baseline.py').read_bytes()
        return checks()

    def promoter(candidate, receipt):
        calls['promote'].append((candidate, receipt))
        return {'state': 'promotion_pending', 'operation_id': 'synthetic-operation'}

    kwargs = dict(verifier=lambda root: {'verified': True}, generator=generate,
                  runner=runner, promoter=promoter, required_checks=CHECKS)
    engine = SelfImprovementEngine(baseline, tmp_path / 'portable-data', **kwargs)
    return engine, calls, kwargs


def test_proposal_is_exact_tested_review_and_only_explicit_approval_promotes(app):
    engine, calls, _ = app
    # Unsigned extras are not copied or included in model context.
    (engine.baseline / 'private-not-in-manifest.txt').write_text('private')
    receipt = engine.propose('Fix addition', TARGET)
    assert receipt['state'] == 'ready_for_approval'
    assert receipt['approval_fingerprint'] == fingerprint(receipt)
    assert '-    return a - b' in receipt['diff']
    assert '+    return a + b' in receipt['diff']
    assert not calls['promote']
    assert len(calls['run']) == 1
    candidate = calls['run'][0][0]
    assert not (candidate / 'private-not-in-manifest.txt').exists()
    assert (engine.baseline / TARGET).read_bytes() == SOURCE
    result = engine.promote(receipt['job_id'], receipt['approval_fingerprint'])
    assert result['state'] == 'promotion_pending'
    # The engine returns the validated durable receipt, not the host's narrower
    # queue response; the UI can bind queuing to this exact approved candidate.
    assert result['job_id'] == receipt['job_id']
    assert result['approval_fingerprint'] == receipt['approval_fingerprint']
    assert 'approval_fingerprint' not in result['promotion']
    assert len(calls['promote']) == 1
    with pytest.raises(ValueError, match='not awaiting approval'):
        engine.promote(receipt['job_id'], receipt['approval_fingerprint'])


@pytest.mark.parametrize('target', ['../calculator.py', '/src/prometheus_assistant/calculator.py',
    'C:/outside.py', 'tests/test_baseline.py', 'pyproject.toml',
    'src/prometheus_assistant/self_improvement.py', 'src/prometheus_assistant/application_policy.py',
    'src/prometheus_assistant/action_approvals.py', 'src/prometheus_assistant/autonomous_promotion.py',
    'src/prometheus_assistant/response_checks.py', 'src/prometheus_assistant/model_benchmarks.py',
    'src/prometheus_assistant/ui/self-improvement.js',
    'src/prometheus_assistant/new_file.py', 'src/prometheus_assistant/.env',
    'src/prometheus_assistant/CON.py'])
def test_rejects_nonimplementation_and_protected_targets_before_generation(app, target):
    engine, calls, _ = app
    receipt = engine.propose('Change this', target)
    assert receipt['state'] == 'rejected'
    assert not calls['generate'] and not calls['run'] and not calls['promote']
    assert not (engine.root / receipt['job_id'] / 'candidate').exists()


def test_model_auto_selection_uses_actual_catalog_and_full_source(app):
    engine, calls, _ = app
    original = engine.generator

    def generator(messages, format):
        if 'catalog' in json.loads(messages[-1]['content']):
            catalog = json.loads(messages[-1]['content'])['catalog']
            assert catalog == [TARGET]
            return {'path': TARGET}
        assert json.loads(messages[-1]['content'])['source'] == SOURCE.decode()
        return original(messages, format)

    engine.generator = generator
    assert engine.propose('Fix addition')['state'] == 'ready_for_approval'


def test_auto_selection_cannot_invent_paths(app):
    engine, calls, _ = app
    engine.generator = lambda messages, format: {'path': 'tests/test_baseline.py'}
    assert engine.propose('Make tests green')['state'] == 'rejected'
    assert not calls['run']


def test_oversize_source_is_rejected_without_truncation_or_generation(app):
    engine, calls, _ = app
    (engine.baseline / TARGET).write_bytes(b'#' + b'x' * 13000)
    manifest(engine.baseline)
    assert engine.propose('Fix addition', TARGET)['state'] == 'rejected'
    assert not calls['generate']


@pytest.mark.parametrize('result', [False, None, {}, {'verified': 1}])
def test_signature_verification_is_required(app, result):
    engine, calls, _ = app
    engine.verifier = lambda root: result
    receipt = engine.propose('Fix addition', TARGET)
    assert receipt['state'] == 'rejected'
    assert not calls['generate']


@pytest.mark.parametrize('mutation', ['hash', 'private', 'duplicate', 'size', 'escape', 'no-tests'])
def test_rejects_invalid_signed_manifest_payload(app, mutation):
    engine, calls, _ = app
    location = engine.baseline / 'SHA256-MANIFEST.json'
    rows = json.loads(location.read_text())
    if mutation == 'hash':
        rows[0]['sha256'] = '0' * 64
    elif mutation == 'private':
        (engine.baseline / 'memory.sqlite3').write_bytes(b'private')
        rows.append({'path': 'memory.sqlite3', 'sha256': sha(b'private')})
    elif mutation == 'duplicate':
        rows.append(dict(rows[0], path=rows[0]['path'].upper()))
    elif mutation == 'size':
        rows[0]['size_bytes'] += 1
    elif mutation == 'escape':
        rows[0]['path'] = '../outside.txt'
    else:
        rows = [row for row in rows if not row['path'].startswith('tests/')]
    location.write_text(json.dumps(rows))
    assert engine.propose('Fix addition', TARGET)['state'] == 'rejected'
    assert not calls['generate'] and not calls['run']


@pytest.mark.parametrize('output', ['```json\n{}\n```', '{"path":"a","path":"b"}',
    '[1]', '{"path":NaN}', {}, dict(patch(), extra='commands'),
    dict(patch(), old_text='missing'), dict(patch(), old_text=''),
    dict(patch(), new_text='return a - b'), dict(patch(), path='tests/test_baseline.py')])
def test_bad_model_patch_stops_after_three_attempts_without_promotion(app, output):
    engine, calls, _ = app
    generated = []
    engine.generator = lambda messages, format: generated.append(messages) or output
    receipt = engine.propose('Fix addition', TARGET)
    assert receipt['state'] == 'failed'
    assert len(generated) == len(receipt['attempts']) == 3
    assert not calls['run'] and not calls['promote']
    assert not (engine.root / receipt['job_id'] / 'candidate').exists()
    assert (engine.baseline / TARGET).read_bytes() == SOURCE


@pytest.mark.parametrize('bad', ['omitted', 'duplicate', 'zero', 'not-executed', 'failed', 'bool-count', 'compile-only'])
def test_no_approval_without_real_complete_regression_receipts(app, bad):
    engine, calls, _ = app
    result = checks()
    if bad == 'omitted':
        result['checks'].pop()
    elif bad == 'duplicate':
        result['checks'].append(result['checks'][0])
    elif bad == 'zero':
        result['checks'][0]['tests_run'] = 0
    elif bad == 'not-executed':
        result['checks'][0]['executed'] = False
    elif bad == 'failed':
        result['checks'][0]['exit_code'] = 1
    elif bad == 'bool-count':
        result['checks'][0]['tests_run'] = True
    else:
        result = {'compiled': True, 'checks': []}
    engine.runner = lambda *args, **kwargs: result
    receipt = engine.propose('Fix addition', TARGET)
    assert receipt['state'] == 'failed'
    assert not calls['promote']


@pytest.mark.parametrize('changed', ['test', 'extra', 'implementation'])
def test_candidate_mutation_by_test_execution_fails_and_is_cleaned(app, changed):
    engine, calls, _ = app

    def runner(candidate, *args, **kwargs):
        relative = {'test': 'tests/test_baseline.py', 'extra': 'extra.py', 'implementation': TARGET}[changed]
        (candidate / relative).write_text('changed by test execution')
        return checks()

    engine.runner = runner
    receipt = engine.propose('Fix addition', TARGET)
    assert receipt['state'] == 'failed'
    assert not calls['promote']
    assert not (engine.root / receipt['job_id'] / 'candidate').exists()


@pytest.mark.parametrize('when', ['before', 'generation', 'tests'])
def test_cancel_never_produces_approval_or_activation(app, when):
    engine, calls, _ = app
    event = threading.Event()
    if when == 'before':
        event.set()
    elif when == 'generation':
        engine.generator = lambda *args: event.set() or patch()
    else:
        engine.runner = lambda *args, **kwargs: event.set() or checks()
    receipt = engine.propose('Fix addition', TARGET, cancel_event=event)
    assert receipt['state'] == 'cancelled'
    assert not calls['promote']
    assert not (engine.root / receipt['job_id'] / 'candidate').exists()


def test_generator_cancellation_is_not_retried(app):
    engine, calls, _ = app
    generated = []

    def generator(*args):
        generated.append(1)
        raise GenerationCancelled('Stopped')

    engine.generator = generator
    assert engine.propose('Fix addition', TARGET)['state'] == 'cancelled'
    assert len(generated) == 1


def test_failed_runner_details_are_preserved_and_inform_the_next_attempt(app):
    engine, calls, _ = app
    runs = []
    generated = []

    def runner(*args, **kwargs):
        result = checks()
        if not runs:
            result['checks'][0].update(exit_code=1, output_tail='assert add(9, 3) == 12 failed')
        runs.append(1)
        return result

    def generator(messages, format):
        generated.append(json.loads(messages[-1]['content']))
        return patch()

    engine.runner, engine.generator = runner, generator
    receipt = engine.propose('Fix addition', TARGET)
    assert receipt['state'] == 'ready_for_approval'
    assert 'assert add(9, 3) == 12 failed' in generated[1]['previous_failure']
    assert receipt['attempts'][0]['test_evidence']['checks'][0]['exit_code'] == 1


def test_repeated_source_span_cannot_choose_an_ambiguous_replacement(app):
    engine, calls, _ = app
    (engine.baseline / TARGET).write_bytes(SOURCE + SOURCE.replace(b'add', b'other'))
    manifest(engine.baseline)
    assert engine.propose('Fix addition', TARGET)['state'] == 'failed'
    assert not calls['run']


def test_carriage_returns_and_non_ascii_source_survive_exact_edit(app):
    engine, calls, _ = app
    body = '# café\r\n'.encode('utf-8') + SOURCE.replace(b'\n', b'\r\n')
    (engine.baseline / TARGET).write_bytes(body)
    manifest(engine.baseline)
    engine.runner = lambda *args, **kwargs: checks()
    receipt = engine.propose('Fix addition', TARGET)
    candidate = engine.root / receipt['job_id'] / 'candidate'
    assert receipt['state'] == 'ready_for_approval'
    assert (candidate / TARGET).read_bytes() == body.replace(b'a - b', b'a + b')


@pytest.mark.parametrize('overrides', [dict(required_checks=()), dict(required_checks=('python', 'python')),
    dict(max_attempts=4), dict(max_attempts=0), dict(max_source_bytes=100000)])
def test_invalid_lifecycle_bounds_are_rejected(app, overrides):
    engine, _, kwargs = app
    with pytest.raises(ValueError):
        SelfImprovementEngine(engine.baseline, engine.root.parent, **(kwargs | overrides))


def test_staging_inside_active_app_is_rejected(app):
    engine, _, kwargs = app
    with pytest.raises(ValueError, match='outside the active'):
        SelfImprovementEngine(engine.baseline, engine.baseline / 'data', **kwargs)


@pytest.mark.parametrize('tamper', ['approval', 'candidate', 'receipt', 'baseline'])
def test_approval_revalidates_exact_patch_tests_and_active_baseline(app, tamper):
    engine, calls, _ = app
    receipt = engine.propose('Fix addition', TARGET)
    fingerprint_value = receipt['approval_fingerprint']
    job = engine.root / receipt['job_id']
    if tamper == 'approval':
        fingerprint_value = '0' * 64
    elif tamper == 'candidate':
        (job / 'candidate' / TARGET).write_text('changed after tests')
    elif tamper == 'receipt':
        stored = json.loads((job / 'receipt.json').read_text())
        stored['checks'][0]['tests_run'] = 999
        (job / 'receipt.json').write_text(json.dumps(stored))
    else:
        (engine.baseline / TARGET).write_bytes(b'# new release\n' + SOURCE)
        manifest(engine.baseline)
    with pytest.raises(ValueError):
        engine.promote(receipt['job_id'], fingerprint_value)
    assert not calls['promote']


def test_failed_activation_consumes_approval_but_keeps_diagnostic_candidate(app):
    engine, calls, _ = app
    receipt = engine.propose('Fix addition', TARGET)
    invoked = []

    def promoter(*args):
        invoked.append(1)
        raise OSError('Helper did not confirm startup')

    engine.promoter = promoter
    result = engine.promote(receipt['job_id'], receipt['approval_fingerprint'])
    assert result['state'] == 'promotion_failed'
    assert (engine.root / receipt['job_id'] / 'candidate').is_dir()
    with pytest.raises(ValueError):
        engine.promote(receipt['job_id'], receipt['approval_fingerprint'])
    assert invoked == [1]


def test_two_engine_instances_cannot_dispatch_same_approval_twice(app):
    engine, calls, kwargs = app
    receipt = engine.propose('Fix addition', TARGET)
    duplicate = SelfImprovementEngine(engine.baseline, engine.root.parent, **kwargs)
    engine.promote(receipt['job_id'], receipt['approval_fingerprint'])
    with pytest.raises(ValueError):
        duplicate.promote(receipt['job_id'], receipt['approval_fingerprint'])
    assert len(calls['promote']) == 1


def test_existing_atomic_claim_rejects_replay_even_if_receipt_state_was_restored(app):
    engine, calls, _ = app
    receipt = engine.propose('Fix addition', TARGET)
    (engine.root / receipt['job_id'] / 'promotion.claim').write_text(receipt['approval_fingerprint'])
    with pytest.raises(FileExistsError):
        engine.promote(receipt['job_id'], receipt['approval_fingerprint'])
    assert not calls['promote']


def test_actual_unchanged_pytest_oracle_rejects_bad_patch_then_accepts_repair(app, tmp_path):
    engine, calls, _ = app
    responses = iter([dict(patch(), new_text='return a * b'), patch()])
    engine.generator = lambda *args: next(responses)
    engine.required_checks = ('python-regression',)
    executed = []

    def runner(candidate, baseline, required, *, cancel_event=None):
        number = len(executed)
        evidence = tmp_path / ('actual-tests-' + str(number))
        evidence.mkdir()
        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        run = subprocess.run([sys.executable, '-B', '-m', 'pytest', 'tests', '-q',
            '-p', 'no:cacheprovider', '--basetemp', str(evidence / 'tmp'),
            '--junitxml', str(evidence / 'result.xml')], cwd=candidate, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        executed.append(run.returncode)
        suite = ET.parse(evidence / 'result.xml').getroot().find('testsuite')
        return {'checks': [{'id': 'python-regression', 'executed': True,
            'exit_code': run.returncode, 'tests_run': int(suite.attrib['tests']),
            'log_sha256': sha(run.stdout)}]}

    engine.runner = runner
    receipt = engine.propose('Correct addition', TARGET)
    assert executed == [1, 0]
    assert receipt['state'] == 'ready_for_approval'
    assert [entry['state'] for entry in receipt['attempts']] == ['failed', 'passed']
    assert not calls['promote']
    assert (engine.baseline / TARGET).read_bytes() == SOURCE
