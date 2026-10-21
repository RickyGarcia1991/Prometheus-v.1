"""Bounded chronological shadow calibration; never edits admission policy.

The caller binds the opaque scope to exact hardware/tool/workload identity and
supplies original, pre-execution forecasts. The first twenty completed pairs per
metric freeze their membership atomically; subsequently completed pairs only
validate, even if their prospective reservation began earlier. Full contexts are retained,
never rolled forward to turn prior holdout data into training data.
"""
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

METRICS = ('direct_ram_gib', 'direct_cpu_seconds', 'job_cpu_seconds')
OUTCOMES = ('completed', 'failed', 'timed_out', 'cancelled', 'unavailable', 'interrupted')
CAPS = dict(direct_ram_gib=2., direct_cpu_seconds=60., job_cpu_seconds=60.)
TRAIN = 20
HOLDOUT = 10
MAX_SCOPES = 8
MAX_RUNS = 100
MAX_BYTES = 16 * 1024 * 1024
VERSION = 'positive-residual-20-10-v1'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _identity(value, size):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value):
        raise ValueError('Calibration requires an opaque hexadecimal identity.')
    return value


def _values(value):
    if type(value) is not dict or set(value) - set(METRICS):
        raise ValueError('Calibration metrics must have explicit, separate scopes.')
    result = {}
    for metric in METRICS:
        number = value.get(metric)
        if number is not None and (type(number) not in (int, float)
                or not 0 <= number <= 1e12 or not math.isfinite(number)):
            raise ValueError('Calibration values must be finite nonnegative bounded numbers.')
        result[metric] = number
    return result


def _score(pairs, correction):
    errors = [actual - (forecast + correction) for forecast, actual in pairs]
    count = len(errors)
    return dict(mae=math.fsum(abs(e)/count for e in errors) if count else None,
        underprediction_count=sum(e > 0 for e in errors),
        max_underprediction=max([0., *errors]),
        total_underprediction=math.fsum(max(0., e) for e in errors),
        mean_overprediction=math.fsum(max(0., -e)/count for e in errors) if count else None)


class CalibrationHistory:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path).absolute(), clock
        self._plain()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Unsupported calibration schema.')
            if version == 0:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    raise ValueError('Unrecognized calibration database.')
                db.execute('CREATE TABLE samples (sequence INTEGER PRIMARY KEY, run TEXT UNIQUE NOT NULL, scope TEXT NOT NULL, observed_at REAL NOT NULL, payload TEXT NOT NULL)')
                db.execute('CREATE TABLE training (scope TEXT NOT NULL, metric TEXT NOT NULL, members TEXT NOT NULL, PRIMARY KEY(scope,metric))')
                db.execute('PRAGMA user_version=1')
            self._rows(db)

    def _plain(self):
        for path in (self.path, *self.path.parents):
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('Linked calibration storage is refused.')
        if self.path.exists() and (not self.path.is_file() or self.path.stat().st_size > MAX_BYTES):
            raise ValueError('Calibration storage exceeds its bound.')

    def _rows(self, db):
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('Unsupported calibration schema.')
        raw = db.execute('SELECT sequence,run,scope,observed_at,payload FROM samples ORDER BY sequence LIMIT ?',
                         (MAX_SCOPES*MAX_RUNS+1,)).fetchall()
        if len(raw) > MAX_SCOPES*MAX_RUNS:
            raise ValueError('Calibration history exceeds its bound.')
        rows, scopes, last_time = [], {}, 0.
        for sequence, run, scope, observed, payload in raw:
            _identity(run, 32); _identity(scope, 64)
            if type(sequence) is not int or sequence <= 0 or type(observed) not in (int, float) or not last_time <= observed <= 253402300799:
                raise ValueError('Invalid calibration ordering.')
            last_time = observed
            if type(payload) is not str or len(payload) > 4096:
                raise ValueError('Invalid bounded calibration record.')
            value = json.loads(payload)
            if (type(value) is not dict or set(value) != {'forecasts', 'actuals', 'outcome', 'pending'}
                    or type(value['pending']) is not bool
                    or value['outcome'] not in OUTCOMES or _json(value) != payload):
                raise ValueError('Invalid calibration record.')
            forecasts, actuals = _values(value['forecasts']), _values(value['actuals'])
            if forecasts != value['forecasts'] or actuals != value['actuals']:
                raise ValueError('Incomplete calibration record schema.')
            if value['pending'] and (value['outcome'] != 'interrupted' or any(v is not None for v in actuals.values())):
                raise ValueError('Invalid pending calibration record.')
            scopes[scope] = scopes.get(scope, 0)+1
            if scopes[scope] > MAX_RUNS or len(scopes) > MAX_SCOPES:
                raise ValueError('Calibration scope capacity exceeded.')
            rows.append(dict(sequence=sequence, run=run, scope=scope, observed_at=observed, **value))
        return rows

    def _freeze(self, db, scope):
        rows = [r for r in self._rows(db) if r['scope'] == scope]
        for metric in METRICS:
            if db.execute('SELECT 1 FROM training WHERE scope=? AND metric=?', (scope, metric)).fetchone():
                continue
            members = [r['run'] for r in rows if r['outcome'] == 'completed'
                and r['forecasts'][metric] is not None and r['actuals'][metric] is not None][:TRAIN]
            if len(members) == TRAIN:
                db.execute('INSERT INTO training VALUES (?,?,?)', (scope, metric, _json(members)))

    def begin(self, run, scope, forecasts):
        """Durably bind a prospective attempt; unresolved attempts censor scope."""
        return self._record(run, scope, forecasts, {}, 'interrupted', pending=True)

    def record(self, run, scope, forecasts, actuals, outcome='completed'):
        return self._record(run, scope, forecasts, actuals, outcome, pending=False)

    def _record(self, run, scope, forecasts, actuals, outcome, *, pending):
        _identity(run, 32); _identity(scope, 64)
        if type(outcome) is not str or outcome not in OUTCOMES:
            raise ValueError('Unknown calibration outcome.')
        payload = _json(dict(forecasts=_values(forecasts), actuals=_values(actuals), outcome=outcome, pending=pending))
        self._plain()
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows = self._rows(db)
            existing = next((r for r in rows if r['run'] == run), None)
            if existing is not None:
                if existing['pending'] and not pending:
                    if existing['scope'] != scope or existing['forecasts'] != _values(forecasts):
                        raise ValueError('Terminal calibration does not match its prospective forecast.')
                    db.execute('UPDATE samples SET payload=? WHERE run=?', (payload, run))
                    self._freeze(db, scope)
                    return dict(recorded=True, reason='completed_pending', policy_tuned=False)
                same = existing['scope'] == scope and _json({k: existing[k] for k in ('forecasts','actuals','outcome','pending')}) == payload
                if not same:
                    raise ValueError('Calibration run identity was reused with conflicting evidence.')
                return dict(recorded=False, reason='duplicate', policy_tuned=False)
            scopes = {r['scope'] for r in rows}
            if scope not in scopes and len(scopes) >= MAX_SCOPES:
                return dict(recorded=False, reason='scope_capacity', policy_tuned=False)
            if sum(r['scope'] == scope for r in rows) >= MAX_RUNS:
                return dict(recorded=False, reason='context_full', policy_tuned=False)
            now = self.clock()
            if type(now) not in (int, float) or not (rows[-1]['observed_at'] if rows else 0) <= now <= 253402300799:
                raise ValueError('Calibration clock moved backwards or is invalid.')
            db.execute('INSERT INTO samples(run,scope,observed_at,payload) VALUES (?,?,?,?)', (run, scope, now, payload))
            self._freeze(db, scope)
        return dict(recorded=True, reason=None, policy_tuned=False)

    def evaluate(self, scope):
        _identity(scope, 64); self._plain()
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            rows = [r for r in self._rows(db) if r['scope'] == scope]
            stored_training = dict(db.execute('SELECT metric,members FROM training WHERE scope=?', (scope,)).fetchall())
        if set(stored_training) - set(METRICS):
            raise ValueError('Unknown calibration training metric.')
        counts = {outcome: sum(r['outcome'] == outcome for r in rows) for outcome in OUTCOMES}
        metrics = {}
        for metric in METRICS:
            complete_rows = [r for r in rows
                if r['outcome'] == 'completed' and r['forecasts'][metric] is not None and r['actuals'][metric] is not None]
            missing = sum(r['outcome'] == 'completed' and (r['forecasts'][metric] is None or r['actuals'][metric] is None) for r in rows)
            frozen = stored_training.get(metric)
            members = json.loads(frozen) if frozen is not None else []
            valid_runs = {r['run']: r for r in complete_rows}
            if frozen is not None and (type(members) is not list or len(members) != TRAIN
                    or any(type(run) is not str or run not in valid_runs for run in members)
                    or len(set(members)) != TRAIN or _json(members) != frozen):
                raise ValueError('Invalid frozen calibration training membership.')
            if len(complete_rows) >= TRAIN and frozen is None:
                raise ValueError('Frozen calibration training membership is missing.')
            train_rows = [valid_runs[run] for run in members] if frozen else complete_rows
            training = [(r['forecasts'][metric], r['actuals'][metric]) for r in train_rows]
            holdout_rows = [r for r in complete_rows if r['run'] not in members] if frozen else []
            holdout = [(r['forecasts'][metric], r['actuals'][metric]) for r in holdout_rows]
            residual = max([0., *(a-p for p,a in training)])
            correction = min(CAPS[metric], residual)
            baseline, candidate = _score(holdout, 0), _score(holdout, correction)
            mean_actual = math.fsum(a/len(holdout) for p,a in holdout) if holdout else None
            allowance = .25*mean_actual if mean_actual is not None else None
            reasons = []
            if len(rows) >= MAX_RUNS: reasons.append('context_full_requires_new_version')
            if any(counts[o] for o in OUTCOMES if o != 'completed'): reasons.append('censored_outcomes')
            if missing: reasons.append('missing_metric_pairs')
            if residual >= CAPS[metric]: reasons.append('correction_cap_saturated')
            enough = len(training) == TRAIN and len(holdout) >= HOLDOUT
            if not enough: reasons.append('insufficient_chronological_samples')
            if enough:
                if not candidate['mae'] < baseline['mae']: reasons.append('no_strict_mae_improvement')
                if not (candidate['underprediction_count'] < baseline['underprediction_count']
                        or candidate['max_underprediction'] < baseline['max_underprediction']):
                    reasons.append('no_underprediction_improvement')
                if candidate['mean_overprediction'] - baseline['mean_overprediction'] > allowance:
                    reasons.append('excess_extra_overprediction')
            hard = [r for r in reasons if r != 'insufficient_chronological_samples']
            metrics[metric] = dict(status='rejected' if hard else 'insufficient' if not enough else 'eligible',
                reason_codes=reasons, correction=correction, correction_kind='additive', cap=CAPS[metric],
                train_samples=len(training), holdout_samples=len(holdout), missing_pairs=missing,
                training_frozen=len(training)==TRAIN, baseline=baseline, candidate=candidate,
                holdout_mean_actual=mean_actual, extra_overprediction_allowance=allowance,
                training_sha256=hashlib.sha256(_json(training).encode()).hexdigest(),
                training_runs_sha256=hashlib.sha256(_json(members).encode()).hexdigest(),
                holdout_sha256=hashlib.sha256(_json(holdout).encode()).hexdigest())
        return dict(schema_version=1, scope=scope, algorithm_version=VERSION,
            dataset_sha256=hashlib.sha256(_json(dict(rows=rows,training=stored_training)).encode()).hexdigest(), counts=counts,
            samples=len(rows), context_full=len(rows)>=MAX_RUNS,
            pending_runs=sum(r['pending'] for r in rows),
            maximum_contexts=MAX_SCOPES, maximum_runs=MAX_RUNS,
            shadow_only=True, policy_tuned=False, metrics=metrics)
