"""Prepare an exact, approved restoration candidate on the Windows host.

This tool never edits the damaged input, a live release, SSD selection, personal
data or signing material. A candidate is a copy of one verified signed edition;
it is not a model-generated edit and is never installed by this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import uuid

VERIFIER_SHA256 = '710d37bf2eb0318e9c20835a9deff5942377cb316c1b5d3bd934be6810419f53'
CERTIFICATE_SHA256 = 'f8526536ab8526c57c68f4cc5b65cc1aba814b99734ee24ad2fd15bb3450967c'
SIGNATURE_FILES = {'SHA256-MANIFEST.json', 'SHA256-MANIFEST.sig', 'MANIFEST-PUBLIC.cer'}
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 4096, 64 * 1024**2, 512 * 1024**2


class RestorationError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2) + '\n').encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def exact_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise RestorationError('An exact lowercase SHA-256 value is required.')
    return value


def relative(name):
    if (not isinstance(name, str) or len(name) > 1000 or not name or '\\' in name
            or PurePosixPath(name).is_absolute()):
        raise RestorationError('Unsafe relative file name.')
    for part in name.split('/'):
        if (part in {'', '.', '..'} or part.endswith(('.', ' ')) or
                re.search(r'[:<>"|?*\x00-\x1f]', part) or
                re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I)):
            raise RestorationError('Unsafe relative file name.')
    return name


def plain(path):
    path = Path(os.path.abspath(path))
    # Inspect from the drive outward: probing a descendant first could already
    # traverse a junction to an unavailable removable volume.
    for item in reversed((path, *path.parents)):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise RestorationError('Linked paths cannot be used for restoration.')
    return path


def host_path(path):
    raw = Path(path)
    if not raw.is_absolute():
        raise RestorationError('An absolute host path is required.')
    if os.name == 'nt' and raw.drive.casefold() != os.environ.get('SystemDrive', 'C:').casefold():
        raise RestorationError('This candidate-only tool accepts paths on the Windows system drive.')
    return plain(raw)


def read(path, limit=MAX_FILE_BYTES):
    path = plain(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise RestorationError('Required file is missing or exceeds its size limit: ' + path.name)
    with path.open('rb') as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise RestorationError('A file grew beyond its size limit.')
    plain(path)
    return body


def strict_json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RestorationError('Duplicate JSON fields are not accepted.')
            result[key] = value
        return result
    def invalid(value):
        raise RestorationError('Non-finite JSON values are not accepted.')
    return json.loads(body, object_pairs_hook=pairs, parse_constant=invalid)


def tree(root):
    root = plain(root)
    if not root.is_dir():
        raise RestorationError('The supplied input directory does not exist.')
    found, seen, total, count = {}, set(), 0, 0
    def scan_failed(error):
        raise RestorationError('An input directory could not be read completely.') from error
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=scan_failed):
        for name in dirs + files:
            plain(Path(folder) / name)
            count += 1
            if count > MAX_FILES * 3:
                raise RestorationError('Input tree exceeds the bounded entry count.')
        for name in files:
            path = Path(folder) / name
            name = relative(path.relative_to(root).as_posix())
            if name.casefold() in seen:
                raise RestorationError('Input contains colliding file names.')
            seen.add(name.casefold())
            body = read(path)
            total += len(body)
            if len(found) >= MAX_FILES + 3 or total > MAX_TOTAL_BYTES:
                raise RestorationError('Input exceeds the bounded file or byte count.')
            found[name] = {'size_bytes': len(body), 'sha256': digest(body)}
    return found


def save_exclusive(path, value):
    path = plain(path)
    with path.open('xb') as stream:
        stream.write(canonical(value))


class RestorationWorkflow:
    def __init__(self, workspace, powershell, powershell_sha256, verifier):
        self.workspace = host_path(workspace)
        self.powershell = host_path(powershell)
        self.powershell_sha256 = exact_hash(powershell_sha256)
        self.verifier = host_path(verifier)
        if not self.workspace.is_dir():
            raise RestorationError('Create the dedicated host workspace first.')
        self._check_tools()

    def _check_tools(self):
        if digest(read(self.powershell)) != self.powershell_sha256:
            raise RestorationError('The approved PowerShell executable changed.')
        if digest(read(self.verifier)) != VERIFIER_SHA256:
            raise RestorationError('The existing trusted verifier changed.')

    def _inside(self, path):
        path = host_path(path)
        if not path.is_relative_to(self.workspace) or path == self.workspace:
            raise RestorationError('Inputs and output jobs must be below the dedicated host workspace.')
        return path

    def _run_verifier(self, root, log):
        self._check_tools()
        env = {key: value for key, value in os.environ.items()
               if key.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
        env['PSModulePath'] = str(self.powershell.parent / 'Modules')
        command = [str(self.powershell), '-NoProfile', '-NonInteractive', '-File', str(self.verifier), '-Root', str(root)]
        with plain(log).open('xb') as output:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=180, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RestorationError('Signed recovery verification failed; see ' + log.name)
        proof = strict_json(read(log, 1_000_000).decode('utf-8-sig'))
        if (not isinstance(proof, dict) or proof.get('healthy') is not True or
                proof.get('signature_verified') is not True or proof.get('repaired') or
                proof.get('memory_modified') is not False or proof.get('network_used') is not False):
            raise RestorationError('The trusted verifier did not confirm an unchanged healthy signed edition.')
        return {'signature_verified': True, 'files': proof['files'], 'log_sha256': digest(read(log, 1_000_000))}

    def _recovery(self, root, manifest_hash, log):
        manifest = read(root / 'SHA256-MANIFEST.json', 1_000_000)
        if digest(manifest) != exact_hash(manifest_hash):
            raise RestorationError('Recovery manifest differs from the independently reviewed edition.')
        if digest(read(root / 'MANIFEST-PUBLIC.cer', 100_000)) != CERTIFICATE_SHA256:
            raise RestorationError('Recovery public certificate differs from the existing trust anchor.')
        rows = strict_json(manifest.decode('utf-8-sig'))
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
            raise RestorationError('Invalid recovery manifest.')
        expected, seen = {}, set()
        for row in rows:
            name = relative(row['path'])
            if (name.casefold() in seen or name in SIGNATURE_FILES or
                    type(row.get('size_bytes')) is not int or not 0 <= row['size_bytes'] <= MAX_FILE_BYTES):
                raise RestorationError('Invalid or duplicate recovery manifest member.')
            seen.add(name.casefold())
            expected[name] = {'size_bytes': row['size_bytes'], 'sha256': exact_hash(row['sha256'].lower())}
        if not {'prometheus.py', 'src/prometheus_assistant/cli.py', 'tools/Verify-Repair-Core.ps1'} <= expected.keys():
            raise RestorationError('The signed edition omits required application files.')
        actual = tree(root)
        for name in SIGNATURE_FILES:
            if name not in actual:
                raise RestorationError('Signed recovery metadata is missing.')
            expected[name] = actual[name]
        if actual != expected:
            raise RestorationError('Recovery payload does not exactly match its signed manifest.')
        proof = self._run_verifier(root, log)
        if tree(root) != actual:
            raise RestorationError('Recovery changed during verification.')
        return actual, proof

    def plan(self, damaged, recovery, manifest_hash):
        damaged, recovery = self._inside(damaged), self._inside(recovery)
        if damaged.is_relative_to(recovery) or recovery.is_relative_to(damaged):
            raise RestorationError('Damaged input and signed recovery must be separate trees.')
        jobs = self._inside(self.workspace / 'restoration-jobs')
        if jobs.is_relative_to(damaged) or jobs.is_relative_to(recovery):
            raise RestorationError('Output jobs cannot be inside an input.')
        jobs.mkdir(exist_ok=True)
        job_id = uuid.uuid4().hex
        job = jobs / job_id
        job.mkdir()
        observed = tree(damaged)
        expected, proof = self._recovery(recovery, manifest_hash, job / 'recovery-verification.log')
        missing = sorted(expected.keys() - observed.keys())
        changed = sorted(name for name in expected.keys() & observed.keys() if expected[name] != observed[name])
        moved, used_targets = [], set()
        for name in sorted(observed.keys() - expected.keys()):
            matches = [target for target in missing if target not in SIGNATURE_FILES and expected[target] == observed[name]]
            if len(matches) != 1 or matches[0] in used_targets:
                raise RestorationError('An unlisted file cannot be uniquely identified as a misplaced signed file: ' + name)
            used_targets.add(matches[0])
            moved.append({'observed': name, 'expected': matches[0], 'sha256': observed[name]['sha256']})
        if not missing and not changed and not moved:
            raise RestorationError('The input already matches the signed edition; no restoration is needed.')
        if tree(damaged) != observed:
            raise RestorationError('The damaged input changed while preparing its review.')
        document = {'schema': 1, 'kind': 'host_restoration_candidate', 'job_id': job_id,
            'workspace': str(self.workspace), 'damaged': str(damaged), 'recovery': str(recovery),
            'manifest_sha256': manifest_hash, 'observed_files': observed, 'recovery_files': expected,
            'tools': {'powershell': str(self.powershell), 'powershell_sha256': self.powershell_sha256,
                      'verifier': str(self.verifier), 'verifier_sha256': VERIFIER_SHA256},
            'actions': {'restore_missing': missing, 'restore_changed': changed, 'recognized_misplacements': moved},
            'reason': 'Restore the canonical file layout and exact bytes of the independently verified signed edition.',
            'effects': 'Create a separate candidate copy on this host. Preserve both inputs byte-for-byte. '
                       'No live installation, SSD selection, personal data, signing or service changes.',
            'recovery_proof': proof, 'created_utc': datetime.now(timezone.utc).isoformat()}
        token = digest(canonical(document))
        save_exclusive(job / 'plan.json', document)
        return {'state': 'needs_approval', 'plan': str(job / 'plan.json'), 'approval_token': token,
                'actions': document['actions'], 'reason': document['reason'], 'effects': document['effects']}

    def stage(self, plan_path, approval_token):
        path = self._inside(plan_path)
        plan = strict_json(read(path, 2_000_000).decode('utf-8'))
        if digest(canonical(plan)) != exact_hash(approval_token):
            raise RestorationError('Approval does not match this exact restoration plan.')
        if (not isinstance(plan, dict) or plan.get('schema') != 1 or plan.get('kind') != 'host_restoration_candidate'
                or path.name != 'plan.json' or path.parent.parent != self.workspace / 'restoration-jobs'
                or path.parent.name != plan.get('job_id') or not re.fullmatch(r'[0-9a-f]{32}', plan['job_id'])):
            raise RestorationError('Invalid restoration plan identity or location.')
        if plan['workspace'] != str(self.workspace) or plan['tools'] != {
                'powershell': str(self.powershell), 'powershell_sha256': self.powershell_sha256,
                'verifier': str(self.verifier), 'verifier_sha256': VERIFIER_SHA256}:
            raise RestorationError('Approved host tools or workspace changed.')
        damaged, recovery = self._inside(plan['damaged']), self._inside(plan['recovery'])
        if tree(damaged) != plan['observed_files'] or tree(recovery) != plan['recovery_files']:
            raise RestorationError('An approved input changed; prepare a new review.')
        job = path.parent
        try:
            with (job / 'approval.claim').open('x', encoding='utf-8') as claim:
                claim.write(approval_token + '\n')
        except FileExistsError as error:
            raise RestorationError('This exact approval was already used.') from error
        result = {'job_id': plan['job_id'], 'approval_token': approval_token, 'state': 'staging'}
        try:
            expected, proof = self._recovery(recovery, plan['manifest_sha256'], job / 'approved-recovery-verification.log')
            if expected != plan['recovery_files']:
                raise RestorationError('The signed recovery edition changed after approval.')
            candidate = job / 'candidate'
            candidate.mkdir(exist_ok=False)
            for name, record in sorted(expected.items()):
                body = read(recovery / relative(name))
                if len(body) != record['size_bytes'] or digest(body) != record['sha256']:
                    raise RestorationError('Recovery changed while preparing the candidate.')
                target = plain(candidate / name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(body)
            if tree(candidate) != expected:
                raise RestorationError('The candidate differs from the approved signed edition.')
            candidate_proof = self._run_verifier(candidate, job / 'candidate-verification.log')
            if (tree(damaged) != plan['observed_files'] or tree(recovery) != expected or tree(candidate) != expected):
                raise RestorationError('Input or candidate changed during final verification.')
            result.update(state='candidate_verified', candidate=str(candidate), manifest_sha256=plan['manifest_sha256'],
                          recovery_proof=proof, candidate_proof=candidate_proof, input_preserved=True,
                          recovery_preserved=True, live_installation_changed=False, scope='Host-only candidate; activation is not implemented.')
        except Exception as error:
            result.update(state='failed', error=str(error)[:1000], live_installation_changed=False)
            save_exclusive(job / 'result.json', result)
            raise RestorationError('Candidate preparation failed; the original input remains untouched. ' + str(error)[:500]) from error
        save_exclusive(job / 'result.json', result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('plan')
    for name in ('workspace', 'damaged', 'recovery', 'powershell', 'verifier'):
        prepare.add_argument('--' + name, required=True, type=Path)
    for name in ('manifest-sha256', 'powershell-sha256'):
        prepare.add_argument('--' + name, required=True)
    stage = commands.add_parser('stage')
    stage.add_argument('--plan', required=True, type=Path)
    stage.add_argument('--approve-token', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            workflow = RestorationWorkflow(args.workspace, args.powershell, args.powershell_sha256, args.verifier)
            result = workflow.plan(args.damaged, args.recovery, args.manifest_sha256)
        else:
            path = host_path(args.plan)
            plan = strict_json(read(path, 2_000_000).decode('utf-8'))
            if digest(canonical(plan)) != exact_hash(args.approve_token):
                raise RestorationError('Approval does not match this exact restoration plan.')
            workflow = RestorationWorkflow(plan['workspace'], plan['tools']['powershell'],
                                           plan['tools']['powershell_sha256'], plan['tools']['verifier'])
            result = workflow.stage(path, args.approve_token)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'failed', 'error': str(error)[:1000], 'live_installation_changed': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
