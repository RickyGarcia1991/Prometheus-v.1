"""Server-only process measurement capture for an already approved apply.

This is not an authorization API. The owner-approved editor calls run_check as
its check hook, once per planned check. Native outcomes are process evidence,
not semantic assertion results or proof of a defect. No stdout is interpreted.
"""
from copy import deepcopy
import hashlib
import math
import uuid

from .advisory_review import _snapshot
from .project_task_evidence import _plan, candidate_digest
from .reviewer_check_evidence import measured_checks

SUPPORTED = frozenset({'python-compile', 'python-unittest', 'python-pytest', 'node-test'})


class ApprovedCheckCapture:
    """Freeze proposed bytes before apply; observe only the existing runner.

    Construct on the server after exact owner approval, before applying files.
    Pass ``capture.run_check`` as the editor's check_runner hook. Call finish()
    after apply (including rollback). Finish never runs tests and is repeatable.
    Keep this object server-owned: serialized input is not authentic evidence.
    """

    def __init__(self, candidate, proposed_files):
        _snapshot(candidate, proposed_files)
        self._files = deepcopy(proposed_files)
        self._plan = deepcopy(_plan(candidate))
        if len(self._plan) > 32 or any(len(c['target']) > 512 or len(c['preset']) > 64 for c in self._plan):
            raise ValueError('Too many planned checks')
        if sum(len(body) for body in proposed_files.values()) > 1_048_576:
            raise ValueError('Proposed snapshot exceeds capture byte limit')
        self._candidate_sha256 = candidate_digest(candidate)
        self._run_id = uuid.uuid4().hex
        self._seen = set()
        self._records = []
        self._issues = []
        self._workspace = None
        self._finished = False

    def _snapshot_matches(self, workspace):
        return all(workspace.read_file(path)['sha256'] == hashlib.sha256(body).hexdigest()
                   for path, body in self._files.items())

    def run_check(self, workspace, preset, target='', **options):
        """Execute one exact planned check through workspace.run_tests unchanged."""
        if self._finished:
            raise ValueError('Capture is already finished')
        planned = {'preset': preset, 'target': target}
        if planned not in self._plan or type(preset) is not str or type(target) is not str:
            raise ValueError('Check is outside the approved plan')
        key = (preset, target)
        if key in self._seen:
            raise ValueError('Repeated check execution is not allowed')
        if self._workspace is not None and workspace is not self._workspace:
            raise ValueError('Capture cannot switch workspaces')
        self._workspace = workspace
        self._seen.add(key)
        with workspace.lock:
            try:
                snapshot_matches = self._snapshot_matches(workspace)
            except (ValueError, OSError):
                snapshot_matches = False
            if not snapshot_matches:
                self._issues.append({**planned, 'reason': 'snapshot_changed_before_check'})
                raise ValueError('Proposed snapshot changed before checking')
            try:
                argv = workspace._command(preset, target)
                result = workspace.run_tests(preset, target, **options)
            except Exception:
                self._issues.append({**planned, 'reason': 'runner_error'})
                raise
            if preset not in SUPPORTED:
                self._issues.append({**planned, 'reason': 'unsupported_process_check'})
                return result
            try:
                snapshot_matches = self._snapshot_matches(workspace)
            except (ValueError, OSError):
                snapshot_matches = False
            if not snapshot_matches:
                self._issues.append({**planned, 'reason': 'snapshot_changed_during_check'})
                return result
            if (type(result) is not dict or result.get('argv') != argv
                    or 'exit_code' not in result
                    or type(result.get('cancelled')) is not bool
                    or type(result.get('timed_out')) is not bool
                    or (result.get('exit_code') is not None and (type(result.get('exit_code')) is not int or result['exit_code'].bit_length() > 64))
                    or type(result.get('duration_seconds')) not in (int, float)
                    or not math.isfinite(result['duration_seconds']) or result['duration_seconds'] < 0):
                self._issues.append({**planned, 'reason': 'invalid_native_result'})
                return result
            actual = {'exit_code': result['exit_code'], 'cancelled': result['cancelled'],
                      'timed_out': result['timed_out']}
            expected = {'exit_code': 0, 'cancelled': False, 'timed_out': False}
            self._records.append({'input': {'kind': 'process_outcome', 'run_id': self._run_id,
                'candidate_sha256': self._candidate_sha256, **planned},
                'expected': expected, 'actual': actual, 'passed': actual == expected})
            return result

    def finish(self):
        """Return detached measurements, explicit gaps, and retained source bytes.

        Zero exit status means process success only. It cannot establish test
        coverage, semantic correctness, or acceptance. In particular this does
        not supersede the editor's existing empty-suite and rollback handling.
        """
        self._finished = True
        issues = deepcopy(self._issues)
        for check in self._plan:
            if (check['preset'], check['target']) not in self._seen:
                issues.append({**check, 'reason': 'check_not_observed'})
        evidence = {}
        # The current digest-keyed protocol cannot represent two identical files.
        # Keep that case explicitly unavailable rather than silently dropping one.
        for path, body in self._files.items():
            digest = hashlib.sha256(body).hexdigest()
            if digest in evidence:
                issues.append({'reason': 'duplicate_source_digest'})
                evidence = {}
                break
            evidence[digest] = {'path': path, 'source_sha256': digest,
                                'checks': deepcopy(self._records)}
        files = deepcopy(self._files) if evidence else {}
        if evidence:
            measured_checks(evidence, files)
        return {'schema_version': 1, 'kind': 'native_process_outcomes',
                'run_id': self._run_id, 'candidate_sha256': self._candidate_sha256,
                'evidence': evidence, 'files': files, 'issues': issues,
                'complete': bool(self._plan) and not issues and len(self._records) == len(self._plan),
                'semantic_evidence_available': False, 'authorized': False,
                'production_accepted': False}
