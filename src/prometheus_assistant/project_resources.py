"""Resource admission for reviewed project checks; no OS process manipulation."""
from contextlib import contextmanager
from dataclasses import asdict
import threading
import time
import shutil
import hashlib
import json
import math
import platform
import os
import tempfile
import uuid
from pathlib import Path
from .prediction_accuracy import AccuracyHistory

from .resource_observations import ResourceProbe
from .resource_scheduler import Cost, Policy, Scheduler, Signals, WorkloadHistory

UNMEASURED = ('GPU headroom', 'thermal events', 'disk I/O pressure',
              'descendant process memory', 'battery drain')


class ProjectResources:
    def __init__(self, root, *, probe=None, clock=time.monotonic, disk_probe=None):
        self.root = root
        self.probe = probe or ResourceProbe(root)
        self.clock = clock
        # Explicitly attached by the primary interface only; workers stay unchanged.
        self.disk_probe = disk_probe
        # Conservative initial admission policy, displayed as uncalibrated.
        self.scheduler = Scheduler(Policy(reserve_ram_gib=2, reserve_disk_gib=.5))
        self.lock = threading.RLock()
        self._accuracy_reservations = {}
        self._requested_mode = None
        self._mode_diagnostic = None
        self._load_mode_preference()

    def _mode_path(self):
        path = Path(self.root) / 'Maintenance/ResourceHistory/mode-preference.json'
        for item in (path, *path.parents):
            if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
                raise ValueError('Linked resource preference paths are refused.')
        return path

    @staticmethod
    def _valid_mode(mode):
        return mode is None or (type(mode) is str and mode in Scheduler.MODES)

    def _load_mode_preference(self):
        try:
            path = self._mode_path()
            if not path.exists():
                return
            with path.open('rb') as stream:
                raw = stream.read(1025)
            if len(raw) > 1024:
                raise ValueError('Oversized preference')
            def pairs(items):
                row = {}
                for key, value in items:
                    if key in row:
                        raise ValueError('Duplicate preference key')
                    row[key] = value
                return row
            row = json.loads(raw, object_pairs_hook=pairs)
            if (type(row) is not dict or set(row) != {'schema_version', 'requested'}
                    or type(row['schema_version']) is not int or row['schema_version'] != 1
                    or not self._valid_mode(row['requested'])):
                raise ValueError('Invalid preference')
            self._requested_mode = row['requested']
        except Exception:
            self._mode_diagnostic = 'Saved mode preference unavailable or invalid; Automatic selected.'

    def _clear_transient_mode_diagnostic(self):
        if self._mode_diagnostic == 'Preference saved; current resource readings unavailable.':
            self._mode_diagnostic = None

    def _mode_report(self):
        return {**self.scheduler.mode_details, 'diagnostic': self._mode_diagnostic}

    def set_mode_preference(self, mode):
        if not self._valid_mode(mode):
            raise ValueError('Choose Automatic or one of the five project scheduling modes.')
        with self.lock:
            path = self._mode_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.mode-', suffix='.tmp', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps({'schema_version': 1, 'requested': mode}).encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                self._mode_path()
                os.replace(temporary, path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            self._requested_mode, self._mode_diagnostic = mode, None
            try:
                observed = self.probe.snapshot(force=True)
                signals = self._signals(observed)
            except Exception:
                # The save succeeded. Unknown health conservatively enters recovery
                # and a fixed diagnostic distinguishes it from a failed write.
                signals = Signals(available_ram_gib=None, free_disk_gib=None, cpu_percent=None)
                self._mode_diagnostic = 'Preference saved; current resource readings unavailable.'
            self.scheduler.mode(signals, now=self.clock(), requested=mode)
            return self._mode_report()

    def _history(self):
        return WorkloadHistory(self.root / 'Maintenance/ResourceHistory/metrics.sqlite3')

    def _category(self, candidate):
        # Host and check families stay local and are hashed; no file paths or prompts.
        identity = [platform.node(), platform.system(), platform.machine(),
                    sorted(check['preset'] for check in candidate.get('checks', []))]
        return 'checks-v1.' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]

    def _job_history(self):
        # Independent bounded retention avoids evicting direct-process samples.
        return WorkloadHistory(self.root / 'Maintenance/ResourceHistory/job-cpu.sqlite3')

    def _job_category(self, candidate):
        return 'job-cpu-v1.' + self._category(candidate).split('.', 1)[1]

    def _job_prediction(self, candidate):
        prediction = self._job_history().predict(self._job_category(candidate), Cost(ram_gib=0))
        return {'cpu_seconds': prediction['cost'].cpu_seconds, 'samples': prediction['samples'],
                'confidence': prediction['confidence'], 'margin': prediction['margin'],
                'scope': 'job_associated_processes',
                'coverage': 'Partial descendant coverage: only processes associated with the Windows job; breakaway or pre-association processes may be absent.',
                'used_for_admission': False}

    def _observe_job_cpu(self, candidate, result):
        report = {'recorded': False, 'scope': 'job_associated_processes',
                  'coverage': 'Partial descendant coverage; associated Windows job processes only.',
                  'used_for_current_admission': False, 'eligible_for_future_admission_after_minimum_samples': True,
                  'disk_io_measured': False,
                  'battery_drain_measured': False}
        planned, checks = candidate.get('checks', []), result.get('checks', [])
        if (result.get('status') != 'passed' or type(planned) is not list or not planned
                or len(planned) > 64 or type(checks) is not list or len(checks) != len(planned)):
            return {**report, 'reason': 'Complete successful planned checks required.'}
        values, seen = [], set()
        for check in planned:
            if type(check) is not dict or type(check.get('preset')) is not str or type(check.get('target')) is not str:
                return {**report, 'reason': 'Invalid reviewed check plan.'}
            key = (check['preset'], check['target'])
            matching = [row for row in checks if type(row) is dict and (row.get('preset'), row.get('target')) == key]
            if key in seen or len(matching) != 1:
                return {**report, 'reason': 'Check measurements do not match the reviewed plan.'}
            seen.add(key)
            row = matching[0]
            if (row.get('passed') is not True or row.get('cancelled') is not False
                    or row.get('timed_out') is not False or type(row.get('exit_code')) is not int or row['exit_code'] != 0):
                return {**report, 'reason': 'Incomplete or unsuccessful checks do not calibrate job CPU.'}
            measured = row.get('resource_measurements')
            job = measured.get('job_metrics') if type(measured) is dict else None
            if (type(job) is not dict or type(job.get('schema_version')) is not int or job['schema_version'] != 1
                    or job.get('scope') != 'job_associated_processes' or job.get('snapshot_complete') is not True
                    or type(job.get('active_processes')) is not int or job['active_processes'] != 0
                    or type(job.get('total_processes')) is not int or not 1 <= job['total_processes'] <= 0xffffffff
                    or type(job.get('cpu_seconds')) not in (int, float)
                    or not 0 <= job['cpu_seconds'] <= 1e300):
                return {**report, 'reason': 'Complete finite job-associated CPU counters unavailable.'}
            values.append(job['cpu_seconds'])
        cpu = sum(values)
        if not math.isfinite(cpu):
            return {**report, 'reason': 'Job CPU total exceeds finite measurement range.'}
        self._job_history().record_observation(self._job_category(candidate), {'cpu_seconds': cpu})
        return {**report, 'recorded': True, 'cpu_seconds': cpu,
                'history_samples': self._job_prediction(candidate)['samples']}

    def _accuracy_history(self):
        return AccuracyHistory(Path(self.root) / 'Maintenance/ResourceHistory/prediction-accuracy.sqlite3')

    @staticmethod
    def _accuracy_identity(candidate):
        return hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def observe(self, candidate, result, *, reservation=None):
        direct = self._observe_direct(candidate, result)
        job = self._observe_job_cpu(candidate, result)
        accuracy = {'recorded': False, 'reason': 'Trusted active reservation unavailable.'}
        try:
            with self.lock:
                bound = self._accuracy_reservations.pop(id(reservation), None)
                if bound is not None and bound['object'] is reservation and bound['candidate'] == self._accuracy_identity(candidate):
                    actuals = {}
                    if direct.get('recorded'):
                        actuals.update(direct_ram_gib=direct.get('ram_gib'), direct_cpu_seconds=direct.get('cpu_seconds'))
                    if job.get('recorded'):
                        actuals['job_cpu_seconds'] = job.get('cpu_seconds')
                    accuracy = self._accuracy_history().record(bound['run'],bound['category'],bound['forecasts'],actuals)
        except Exception:
            accuracy = {'recorded': False, 'reason': 'Optional accuracy diagnostics unavailable.'}
        return {**direct, 'job_cpu_observation': job, 'prediction_accuracy': accuracy}

    def _observe_direct(self, candidate, result):
        """Record trusted complete runner counters, never inferred resource usage."""
        checks, planned = result.get('checks', []), candidate.get('checks', [])
        report = {'recorded': False, 'scope': 'direct test processes only; descendants unmeasured',
                  'disk_io_measured': False, 'battery_drain_measured': False}
        if not planned or not isinstance(checks, list) or len(checks) != len(planned):
            return {**report, 'reason': 'Complete check measurements unavailable.'}
        if result.get('status') != 'passed':
            return {**report, 'reason': 'Incomplete or unsuccessful workloads do not calibrate estimates.'}
        measurements = []
        for check in planned:
            matching = [row for row in checks if row.get('preset') == check['preset'] and row.get('target') == check['target']]
            if len(matching) != 1:
                return {**report, 'reason': 'Check measurements do not match the reviewed plan.'}
            row = matching[0]
            if (row.get('passed') is not True or row.get('cancelled') is not False
                    or row.get('timed_out') is not False or type(row.get('exit_code')) is not int
                    or row['exit_code'] != 0):
                return {**report, 'reason': 'Incomplete or unsuccessful checks do not calibrate estimates.'}
            measured = row.get('resource_measurements', {})
            if measured.get('scope') != 'direct_process':
                return {**report, 'reason': 'Direct process measurements unavailable.'}
            measurements.append(measured)
        def complete(name, combine):
            values = [row.get(name) for row in measurements]
            return combine(values) if all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values) else None
        # Checks run serially; retain the largest direct-process working set.
        ram, cpu = complete('ram_gib', max), complete('cpu_seconds', sum)
        if ram is None or ram <= 0:
            return {**report, 'reason': 'Peak working-set measurements unavailable.'}
        self._history().record_observation(self._category(candidate), {'ram_gib': ram, 'cpu_seconds': cpu})
        return {**report, 'recorded': True, 'ram_gib': ram, 'cpu_seconds': cpu,
                'io_mib': complete('io_mib', sum),
                'io_scope': 'OS process transfer bytes, including non-disk I/O',
                'history_samples': self._history().predict(self._category(candidate), Cost(ram_gib=0))['samples']}

    def _signals(self, observation):
        return Signals(**{key: observation.get(key) for key in
            ('available_ram_gib', 'free_disk_gib', 'cpu_percent', 'battery_percent', 'plugged_in')})

    def select_project(self, root):
        # The UI supplies the validated root after a successful Code open.
        # Do not hold scheduler/app locks while waiting for an in-flight read.
        if self.disk_probe is not None:
            self.disk_probe.select_project(root)

    def specialist_snapshot(self):
        """One fresh foreground sample; no disk helper, history or model action."""
        with self.lock:
            raw = self.probe.snapshot(force=True)
            if type(raw) is not dict:
                raise ValueError('Resource sample unavailable')
            self.scheduler.mode(self._signals(raw), now=self.clock(), foreground=True,
                                requested=self._requested_mode)
            self._clear_transient_mode_diagnostic()
            return {'observations': dict(raw), 'mode': {
                key: self.scheduler.mode_details[key]
                for key in ('requested', 'effective', 'dwell_pending')}}

    def status(self, foreground=False):
        with self.lock:
            observed = self.probe.snapshot()
            mode = self.scheduler.mode(self._signals(observed), now=self.clock(), foreground=foreground, requested=self._requested_mode)
            self._clear_transient_mode_diagnostic()
            result = {'mode': mode, 'mode_preference': self._mode_report(), 'observations': observed,
                    'policy': asdict(self.scheduler.policy), 'policy_calibrated': False,
                    'scope': 'Admission for approved project edits and checks only',
                    'active_leases': len(self.scheduler.leases),
                    'unmeasured': list(UNMEASURED)}
        # Native sampling may wait for its bounded child. Never hold the scheduler
        # lock, and never make these observations an input to admission or history.
        if self.disk_probe is not None:
            result['disk_observation'] = self.disk_probe.snapshot()
        return result

    def review(self, candidate, *, refresh=False):
        with self.lock:
            items = candidate.get('candidates', [candidate])
            size = sum(len(item.get('content', '').encode('utf-8')) for item in items)
            # Storage covers new contents and recovery copies. RAM is a floor for
            # unknown project checks, not a claim of their measured peak usage.
            baseline = Cost(ram_gib=.25, disk_gib=max(.001, size * 4 / 1024**3))
            prediction = self._history().predict(self._category(candidate), baseline)
            job_prediction = self._job_prediction(candidate)
            cpu_sources = []
            for source, cpu, samples in [('direct_process', prediction['cost'].cpu_seconds, prediction['cpu_samples']),
                    ('job_associated_processes', job_prediction['cpu_seconds'], job_prediction['samples'])]:
                if samples >= 5 and type(cpu) in (int, float) and 0 <= cpu <= 1e300:
                    cpu_sources.append({'source': source, 'cpu_seconds': cpu, 'samples': samples})
            learned_cpu = max((row['cpu_seconds'] for row in cpu_sources), default=None)
            selected = max(cpu_sources, key=lambda row: row['cpu_seconds'], default=None)
            cpu_evidence = {'learned_cpu_seconds': learned_cpu, 'eligible_sources': cpu_sources,
                'source': selected['source'] if selected else None,
                'samples': selected['samples'] if selected else 0,
                'reason': 'Eligible measured CPU maximum plus margin.' if selected else 'Fewer than five valid CPU samples per source; existing policy only.',
                'minimum_samples': 5, 'combination': 'maximum, never sum', 'policy_calibrated': False,
                'scope': 'CPU execution time; not wall time or CPU percent. Partial job coverage can only tighten admission.'}
            job_prediction['used_for_admission'] = selected is not None and selected['source'] == 'job_associated_processes'
            observed = self.probe.snapshot(force=refresh)
            try:
                project_free = shutil.disk_usage(items[0]['project']).free / 1024**3
                observed['free_disk_gib'] = min(observed['free_disk_gib'], project_free) if observed['free_disk_gib'] is not None else None
            except (OSError, KeyError):
                observed['free_disk_gib'] = None
            assessment = self.scheduler.assess(self._signals(observed), prediction['cost'],
                                               now=self.clock(), foreground=True, requested=self._requested_mode, learned_cpu_seconds=learned_cpu)
            self._clear_transient_mode_diagnostic()
            return {**assessment, 'mode_preference': self._mode_report(), 'observed_at': observed.get('observed_at'),
                    'admission_signals': asdict(self._signals(observed)),
                    'policy': asdict(self.scheduler.policy), 'policy_calibrated': False,
                    'confidence': prediction['confidence'], 'history_samples': prediction['samples'],
                    'job_cpu_prediction': job_prediction, 'cpu_admission': cpu_evidence,
                    'prediction_accuracy': self._accuracy_summary(candidate),
                    'estimate_basis': 'Conservative baseline plus retained direct-process peak working sets and margin; descendants unmeasured.',
                    'enforcement': 'Admission only; this is not an OS quota or a guarantee of peak usage.',
                    'unmeasured': list(UNMEASURED)}

    def _accuracy_summary(self, candidate):
        try:
            return self._accuracy_history().summary(self._category(candidate))
        except Exception:
            return {'available': False, 'reason': 'Optional accuracy diagnostics unavailable.'}

    @contextmanager
    def reserve(self, candidate, task_id):
        with self.lock:
            assessment = self.review(candidate, refresh=True)
            result = self.scheduler.acquire(task_id, Signals(**assessment['admission_signals']),
                Cost(**assessment['estimated_cost']), now=self.clock(), foreground=True,
                learned_cpu_seconds=assessment['cpu_admission']['learned_cpu_seconds'], requested=self._requested_mode)
            if result['decision'] != 'run_local':
                raise ValueError('Project task deferred before changing files: ' + ' '.join(result['reasons'])
                                 + ' Refresh resource readings and prepare a new review.')
        reservation = {**assessment, **result}
        try:
            # Numeric snapshot is server-owned; mutating/copying the public dict
            # cannot replace it. No request-supplied run identity is accepted.
            try:
                with self.lock:
                    self._accuracy_reservations[id(reservation)] = dict(object=reservation,
                        run=uuid.uuid4().hex, candidate=self._accuracy_identity(candidate),
                        category=self._category(candidate), forecasts=dict(
                            direct_ram_gib=assessment['estimated_cost']['ram_gib'],
                            direct_cpu_seconds=assessment['estimated_cost']['cpu_seconds'],
                            job_cpu_seconds=assessment['job_cpu_prediction']['cpu_seconds']))
            except Exception:
                # Optional diagnostics must not prevent approved execution.
                pass
            yield reservation
        finally:
            with self.lock:
                self._accuracy_reservations.pop(id(reservation), None)
            self.scheduler.release(task_id)


def service(app):
    # Interface creates this eagerly; lightweight test doubles may initialize it here.
    with app.lock:
        if not hasattr(app, 'project_resources'):
            app.project_resources = ProjectResources(app.data_root)
        return app.project_resources
