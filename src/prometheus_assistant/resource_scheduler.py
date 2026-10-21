"""Deterministic resource policy and bounded local measurements.

This foundation does not change OS settings or dispatch jobs. Callers supply
measured signals and explicit budgets. Missing measurements remain unknown.
"""
from dataclasses import asdict, dataclass, fields
import math
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import threading


def _number(value, optional=False):
    if value is None and optional:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Resource measurements must be finite nonnegative numbers.')


@dataclass(frozen=True)
class Cost:
    ram_gib: float
    disk_gib: float = 0
    cpu_seconds: float | None = None
    disk_io_mib: float | None = None
    battery_percent: float | None = None
    vram_gib: float | None = None

    def __post_init__(self):
        for field in fields(self):
            _number(getattr(self, field.name), field.name not in {'ram_gib', 'disk_gib'})


@dataclass(frozen=True)
class Signals:
    available_ram_gib: float | None
    free_disk_gib: float | None
    cpu_percent: float | None = None
    battery_percent: float | None = None
    plugged_in: bool | None = None
    thermal_distress: bool = False
    low_memory: bool = False

    def __post_init__(self):
        for name in ('available_ram_gib', 'free_disk_gib', 'cpu_percent', 'battery_percent'):
            value = getattr(self, name)
            _number(value, True)
            if name.endswith('percent') and value is not None and value > 100:
                raise ValueError('Percent must be between zero and 100.')
        if self.plugged_in is not None and type(self.plugged_in) is not bool:
            raise ValueError('Power source must be boolean or unknown.')
        if type(self.thermal_distress) is not bool or type(self.low_memory) is not bool:
            raise ValueError('Pressure flags must be boolean.')


@dataclass(frozen=True)
class Policy:
    reserve_ram_gib: float
    reserve_disk_gib: float
    dwell_seconds: float = 30
    battery_low: float = 30
    battery_recover: float = 40
    cpu_ceiling: float = 85
    heavy_cpu_seconds: float = 30
    heavy_cpu_ceiling: float = 60

    def __post_init__(self):
        for field in fields(self):
            _number(getattr(self, field.name))
        if not 0 < self.battery_low < self.battery_recover <= 100 or not 0 < self.cpu_ceiling <= 100:
            raise ValueError('Invalid policy thresholds.')
        if self.heavy_cpu_seconds <= 0 or not 0 < self.heavy_cpu_ceiling <= 100:
            raise ValueError('Invalid learned CPU thresholds.')


class Scheduler:
    MODES = ('Idle', 'Balanced', 'Performance', 'Battery Saver', 'Critical Recovery')

    def __init__(self, policy):
        self.policy = policy
        self.current = None
        self.mode_details = {}
        self.changed_at = 0
        self.last_time = 0
        self.leases = {}
        self.lock = threading.RLock()

    def mode(self, signals, *, now, foreground=False, requested=None):
        _number(now)
        if requested is not None and requested not in self.MODES:
            raise ValueError('Unknown resource mode.')
        with self.lock:
            if now < self.last_time:
                raise ValueError('Scheduler clock must be monotonic.')
            self.last_time = now
            p = self.policy
            critical = (signals.low_memory or signals.thermal_distress
                or signals.available_ram_gib is None or signals.free_disk_gib is None
                or signals.available_ram_gib < p.reserve_ram_gib
                or signals.free_disk_gib < p.reserve_disk_gib)
            battery = signals.plugged_in is False and (signals.battery_percent is None
                or signals.battery_percent <= p.battery_low
                or (self.current == 'Battery Saver' and signals.battery_percent < p.battery_recover))
            desired = ('Critical Recovery' if critical or requested == 'Critical Recovery' else 'Battery Saver' if battery
                       else requested or ('Balanced' if foreground else 'Idle'))
            immediate = critical or (battery and self.current != 'Critical Recovery') or desired == 'Critical Recovery'
            if self.current is None or (desired != self.current and
                    (immediate or now - self.changed_at >= p.dwell_seconds)):
                self.current, self.changed_at = desired, now
            preferred = requested or ('Balanced' if foreground else 'Idle')
            self.mode_details = dict(requested=requested, effective=self.current,
                reason=('Critical resource protection.' if critical else 'Owner Critical Recovery pause.' if requested == 'Critical Recovery' else 'Battery protection.' if battery
                        else 'Owner scheduling preference.' if requested else 'Automatic foreground/idle selection.'),
                protective_override=(critical or battery) and desired != preferred,
                dwell_pending=self.current != desired)
            if self.current != desired:
                self.mode_details['reason'] += ' Minimum mode dwell time is still active.'
            return self.current

    def assess(self, signals, cost, *, now, foreground=False, requested=None, learned_cpu_seconds=None):
        if learned_cpu_seconds is not None and (type(learned_cpu_seconds) not in (int, float)
                or not 0 <= learned_cpu_seconds <= 1e300):
            raise ValueError('Learned CPU seconds must be finite nonnegative bounded numbers.')
        with self.lock:
            mode = self.mode(signals, now=now, foreground=foreground, requested=requested)
            unknown = [f.name for f in fields(signals) if getattr(signals, f.name) is None]
            reasons = []
            if mode == 'Critical Recovery':
                reasons.append('Owner selected Critical Recovery; new project work is paused.'
                    if self.mode_details.get('reason') == 'Owner Critical Recovery pause.'
                    else 'Resource health is insufficient, unknown, or still in recovery dwell.')
            used_ram = sum(c.ram_gib for c in self.leases.values())
            used_disk = sum(c.disk_gib for c in self.leases.values())
            if signals.available_ram_gib is not None and cost.ram_gib + used_ram + self.policy.reserve_ram_gib > signals.available_ram_gib:
                reasons.append('RAM estimate exceeds headroom after active reservations.')
            if signals.free_disk_gib is not None and cost.disk_gib + used_disk + self.policy.reserve_disk_gib > signals.free_disk_gib:
                reasons.append('Disk estimate exceeds reserved free space.')
            if signals.cpu_percent is None or signals.cpu_percent >= self.policy.cpu_ceiling:
                reasons.append('CPU headroom is unavailable.')
            if (learned_cpu_seconds is not None and learned_cpu_seconds >= self.policy.heavy_cpu_seconds
                    and signals.cpu_percent is not None and signals.cpu_percent >= self.policy.heavy_cpu_ceiling):
                reasons.append('Learned CPU-intensive check workload deferred while current CPU load is high.')
            if signals.plugged_in is not True:
                if signals.battery_percent is None or cost.battery_percent is None:
                    reasons.append('Battery budget is unknown.')
                elif signals.battery_percent - cost.battery_percent < self.policy.battery_low:
                    reasons.append('Estimated battery use exceeds the configured reserve.')
            return {'decision': 'defer' if reasons else 'run_local', 'mode': mode,
                    'reasons': reasons, 'unknown_metrics': unknown, 'estimated_cost': asdict(cost),
                    'active_leases': len(self.leases), 'dispatch_performed': False}

    def acquire(self, task_id, signals, cost, **kwargs):
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', task_id):
            raise ValueError('A bounded task identifier is required.')
        with self.lock:
            if task_id in self.leases:
                raise ValueError('Task already holds a resource reservation.')
            result = self.assess(signals, cost, **kwargs)
            if result['decision'] == 'run_local':
                self.leases[task_id] = cost
            return result

    def release(self, task_id):
        with self.lock:
            return self.leases.pop(task_id, None) is not None


class WorkloadHistory:
    """Bounded measurements only; no prompts, source paths, or task payloads."""
    def __init__(self, path, *, limit=500):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('History limit must be between 1 and 10000.')
        self.path, self.limit = Path(path), limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ram_gib REAL, disk_gib REAL, cpu_seconds REAL, disk_io_mib REAL, battery_percent REAL, vram_gib REAL)')

    @staticmethod
    def _kind(kind):
        if not isinstance(kind, str) or not re.fullmatch(r'[a-z0-9_.-]{1,64}', kind):
            raise ValueError('Use a bounded workload category, not a prompt or path.')

    def record(self, kind, measured):
        self.record_observation(kind, asdict(measured))

    def record_observation(self, kind, measured):
        """Persist only available measurements; unknown counters remain NULL."""
        self._kind(kind)
        names = [f.name for f in fields(Cost)]
        if not isinstance(measured, dict) or set(measured) - set(names):
            raise ValueError('Unknown resource measurement.')
        values = [measured.get(name) for name in names]
        for value in values:
            _number(value, True)
        if not any(value is not None for value in values):
            raise ValueError('At least one measured resource is required.')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO samples(kind,ram_gib,disk_gib,cpu_seconds,disk_io_mib,battery_percent,vram_gib) VALUES (?,?,?,?,?,?,?)',
                       (kind, *values))
            db.execute('DELETE FROM samples WHERE id NOT IN (SELECT id FROM samples ORDER BY id DESC LIMIT ?)', (self.limit,))

    def predict(self, kind, baseline):
        self._kind(kind)
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute('SELECT ram_gib,disk_gib,cpu_seconds,disk_io_mib,battery_percent,vram_gib FROM samples WHERE kind=? ORDER BY id DESC LIMIT ?',
                              (kind, self.limit)).fetchall()
        values = {}
        margin = 1.5 if len(rows) < 5 else 1.2
        for index, field in enumerate(fields(Cost)):
            observed = [row[index] for row in rows if row[index] is not None]
            base = getattr(baseline, field.name)
            if base is not None:
                observed.append(base)
            values[field.name] = max(observed) * margin if observed else None
        return {'cost': Cost(**values), 'samples': len(rows), 'cpu_samples': sum(row[2] is not None for row in rows), 'confidence': 'low' if len(rows) < 5 else 'measured',
                'margin': margin, 'method': 'retained maximum plus margin'}
