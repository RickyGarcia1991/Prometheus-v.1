"""Host candidate boundary checks; real RSA verification is qualified separately."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('core_restoration', Path(__file__).parents[1] / 'tools/prepare-core-restoration.py')
restoration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restoration)


@pytest.fixture
def case(tmp_path, monkeypatch):
    recovery = tmp_path / 'recovery'
    recovery.mkdir()
    payload = {
        'prometheus.py': b'# entry\n',
        'src/prometheus_assistant/cli.py': b'# cli\n',
        'tools/Verify-Repair-Core.ps1': b'# verifier\n',
        'src/example.py': b'VALUE = 42\n',
    }
    for name, body in payload.items():
        target = recovery / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    manifest = restoration.canonical([{'path': name, 'sha256': restoration.digest(body), 'size_bytes': len(body)}
                                      for name, body in payload.items()])
    (recovery / 'SHA256-MANIFEST.json').write_bytes(manifest)
    (recovery / 'SHA256-MANIFEST.sig').write_bytes(b'unit-fixture-signature')
    (recovery / 'MANIFEST-PUBLIC.cer').write_bytes(b'unit-fixture-certificate')
    damaged = tmp_path / 'damaged'
    shutil.copytree(recovery, damaged)
    powershell = tmp_path / 'powershell.exe'
    powershell.write_bytes(b'unit-fixture-runtime')
    verifier = tmp_path / 'verifier.ps1'
    verifier.write_bytes(b'unit-fixture-verifier')
    monkeypatch.setattr(restoration, 'VERIFIER_SHA256', restoration.digest(verifier.read_bytes()))
    monkeypatch.setattr(restoration, 'CERTIFICATE_SHA256', restoration.digest(b'unit-fixture-certificate'))
    calls = []
    def verify(self, root, log):
        calls.append(root)
        if (root / 'SHA256-MANIFEST.sig').read_bytes() != b'unit-fixture-signature':
            raise restoration.RestorationError('Signature rejected by fixture verifier.')
        log.write_text('fixture verifier only')
        return {'signature_verified': True, 'files': len(payload), 'log_sha256': restoration.digest(log.read_bytes())}
    monkeypatch.setattr(restoration.RestorationWorkflow, '_run_verifier', verify)
    workflow = restoration.RestorationWorkflow(tmp_path, powershell, restoration.digest(powershell.read_bytes()), verifier)
    return {'workspace': tmp_path, 'recovery': recovery, 'damaged': damaged, 'manifest': restoration.digest(manifest),
            'workflow': workflow, 'calls': calls, 'powershell': powershell, 'verifier': verifier}


def review(case):
    return case['workflow'].plan(case['damaged'], case['recovery'], case['manifest'])


def lose_file(case):
    (case['damaged'] / 'src/example.py').unlink()


def stage(case, plan):
    return case['workflow'].stage(plan['plan'], plan['approval_token'])


def test_missing_changed_and_misplaced_prepare_exact_candidate_only(case):
    damaged = case['damaged']
    (damaged / 'src/example.py').rename(damaged / 'misplaced.py')
    (damaged / 'prometheus.py').write_bytes(b'broken entry')
    (damaged / 'src/prometheus_assistant/cli.py').unlink()
    before = restoration.tree(damaged)
    recovery_before = restoration.tree(case['recovery'])
    plan = review(case)
    assert plan['state'] == 'needs_approval'
    assert plan['actions']['restore_changed'] == ['prometheus.py']
    assert plan['actions']['restore_missing'] == ['src/example.py', 'src/prometheus_assistant/cli.py']
    assert plan['actions']['recognized_misplacements'][0]['observed'] == 'misplaced.py'
    assert not (Path(plan['plan']).parent / 'candidate').exists()
    result = stage(case, plan)
    assert result['state'] == 'candidate_verified'
    assert result['live_installation_changed'] is False
    assert restoration.tree(Path(result['candidate'])) == recovery_before
    assert restoration.tree(damaged) == before
    assert restoration.tree(case['recovery']) == recovery_before
    assert len(case['calls']) == 3
    assert not (Path(result['candidate']) / 'misplaced.py').exists()


def test_healthy_input_does_not_stage_redundant_candidate(case):
    with pytest.raises(restoration.RestorationError, match='no restoration is needed'):
        review(case)
    assert not list(case['workspace'].glob('restoration-jobs/*/candidate'))


@pytest.mark.parametrize('extra', [b'unsigned executable', b'# entry\n'])
def test_unknown_extra_even_duplicate_of_present_payload_is_refused(case, extra):
    lose_file(case)
    (case['damaged'] / 'unknown.py').write_bytes(extra)
    with pytest.raises(restoration.RestorationError, match='uniquely identified'):
        review(case)


def test_two_extra_copies_for_one_missing_file_are_ambiguous(case):
    body = (case['damaged'] / 'src/example.py').read_bytes()
    lose_file(case)
    for name in ('one.py', 'two.py'):
        (case['damaged'] / name).write_bytes(body)
    with pytest.raises(restoration.RestorationError, match='uniquely identified'):
        review(case)


@pytest.mark.parametrize('corrupt', ['payload', 'certificate', 'signature', 'manifest', 'extra'])
def test_corrupt_recovery_cannot_prepare_review(case, corrupt):
    lose_file(case)
    paths = {'payload': 'prometheus.py', 'certificate': 'MANIFEST-PUBLIC.cer',
             'signature': 'SHA256-MANIFEST.sig', 'manifest': 'SHA256-MANIFEST.json', 'extra': 'unknown.py'}
    (case['recovery'] / paths[corrupt]).write_bytes(b'wrong')
    with pytest.raises(restoration.RestorationError):
        review(case)
    assert not list(case['workspace'].glob('restoration-jobs/*/plan.json'))


def test_wrong_approval_does_not_consume_correct_approval(case):
    lose_file(case)
    plan = review(case)
    with pytest.raises(restoration.RestorationError, match='Approval does not match'):
        case['workflow'].stage(plan['plan'], '0' * 64)
    assert not (Path(plan['plan']).parent / 'approval.claim').exists()
    assert stage(case, plan)['state'] == 'candidate_verified'


def test_changed_plan_invalidates_exact_approval(case):
    lose_file(case)
    plan = review(case)
    path = Path(plan['plan'])
    body = json.loads(path.read_text())
    body['reason'] = 'different effects'
    path.write_bytes(restoration.canonical(body))
    with pytest.raises(restoration.RestorationError, match='Approval does not match'):
        stage(case, plan)
    assert not (path.parent / 'approval.claim').exists()


@pytest.mark.parametrize('input_name', ['damaged', 'recovery'])
def test_changed_input_requires_fresh_review(case, input_name):
    lose_file(case)
    plan = review(case)
    (case[input_name] / 'prometheus.py').write_bytes(b'changed after approval')
    with pytest.raises(restoration.RestorationError, match='approved input changed'):
        stage(case, plan)
    assert not (Path(plan['plan']).parent / 'approval.claim').exists()


def test_used_approval_cannot_replay(case):
    lose_file(case)
    plan = review(case)
    stage(case, plan)
    with pytest.raises(restoration.RestorationError, match='already used'):
        stage(case, plan)


def test_candidate_verification_failure_consumes_approval_and_preserves_input(case, monkeypatch):
    lose_file(case)
    plan = review(case)
    before = restoration.tree(case['damaged'])
    original = restoration.RestorationWorkflow._run_verifier
    def fail_candidate(self, root, log):
        if root.name == 'candidate':
            raise restoration.RestorationError('Injected candidate verification failure')
        return original(self, root, log)
    monkeypatch.setattr(restoration.RestorationWorkflow, '_run_verifier', fail_candidate)
    with pytest.raises(restoration.RestorationError, match='Candidate preparation failed'):
        stage(case, plan)
    job = Path(plan['plan']).parent
    assert json.loads((job / 'result.json').read_text())['state'] == 'failed'
    assert restoration.tree(case['damaged']) == before
    with pytest.raises(restoration.RestorationError, match='already used'):
        stage(case, plan)


def test_tool_substitution_rejected(case):
    case['verifier'].write_bytes(b'substitution')
    with pytest.raises(restoration.RestorationError, match='verifier changed'):
        case['workflow']._check_tools()


def test_paths_must_be_bounded_and_separate(case, tmp_path):
    with pytest.raises(restoration.RestorationError, match='absolute host'):
        case['workflow']._inside(Path('relative'))
    with pytest.raises(restoration.RestorationError, match='below the dedicated'):
        case['workflow']._inside(tmp_path.parent)
    with pytest.raises(restoration.RestorationError, match='separate trees'):
        case['workflow'].plan(case['recovery'], case['recovery'], case['manifest'])


@pytest.mark.parametrize('name', ['../escape.py', '/absolute', 'a\\b', 'a//b', 'a/CON.txt', 'a:stream', 'a./b'])
def test_unsafe_manifest_names_rejected(name):
    with pytest.raises(restoration.RestorationError, match='Unsafe relative'):
        restoration.relative(name)


def test_linked_input_refused(case, monkeypatch):
    original = Path.lstat
    monkeypatch.setattr(Path, 'lstat', lambda self: SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
                        if self == case['damaged'] / 'prometheus.py' else original(self))
    with pytest.raises(restoration.RestorationError, match='Linked paths'):
        review(case)


def test_unreadable_directory_is_not_silently_treated_as_missing(case, monkeypatch):
    def incomplete_walk(root, **kwargs):
        kwargs['onerror'](PermissionError('fixture unreadable directory'))
        yield root, [], []
    monkeypatch.setattr(restoration.os, 'walk', incomplete_walk)
    with pytest.raises(restoration.RestorationError, match='could not be read completely'):
        restoration.tree(case['damaged'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows drive routing contract')
def test_other_drive_rejected_before_any_filesystem_probe(monkeypatch):
    def should_not_probe(path):
        pytest.fail('A non-host drive was probed before it was rejected')
    monkeypatch.setattr(restoration, 'plain', should_not_probe)
    system_drive = os.environ.get('SystemDrive', 'C:').casefold()
    other_drive = 'Z:' if system_drive != 'z:' else 'Y:'
    with pytest.raises(restoration.RestorationError, match='system drive'):
        restoration.host_path(Path(other_drive + '/never-probe'))


def test_linked_ancestor_rejected_before_descendant_probe(tmp_path, monkeypatch):
    linked = tmp_path / 'linked'
    descendant = linked / 'untrusted-volume' / 'file.py'
    original = Path.lstat
    probes = []
    def observed(self):
        probes.append(self)
        if self == linked:
            raise restoration.RestorationError('Linked paths are refused by fixture')
        if self.is_relative_to(linked):
            pytest.fail('A descendant of the linked ancestor was probed')
        return original(self)
    monkeypatch.setattr(Path, 'lstat', observed)
    with pytest.raises(restoration.RestorationError, match='Linked paths'):
        restoration.plain(descendant)
    assert linked in probes


@pytest.mark.parametrize('body', ['{"a":1,"a":2}', '{"a":NaN}'])
def test_ambiguous_json_is_refused(body):
    with pytest.raises(restoration.RestorationError):
        restoration.strict_json(body)


def test_cli_failure_is_bounded_json_without_traceback(tmp_path):
    absent = tmp_path / 'absent.json'
    result = subprocess.run([sys.executable, '-B', str(SPEC.origin), 'stage', '--plan', str(absent),
                             '--approve-token', '0' * 64], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout)['state'] == 'failed'
    assert 'Traceback' not in result.stderr + result.stdout
