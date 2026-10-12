"""Bounded, request-local timings. Never retain arguments, results or errors."""
from contextlib import contextmanager
from contextvars import ContextVar
import time


STAGES = frozenset({
    'reference_cache', 'retrieval', 'model_inventory', 'hardware', 'resident_memory',
    'eligibility', 'runtime_readiness', 'runtime_client_setup', 'history_open',
    'history_read', 'context_preparation', 'model_readiness', 'generation',
    'quality_summary', 'history_persistence', 'quality_recording', 'model_unload',
})
MAX_STAGES = 64
_current = ContextVar('prometheus_request_timing', default=None)


class RequestTiming:
    def __init__(self, *, clock=None, started=None, cancelled_exceptions=()):
        self.clock = clock or time.monotonic
        self.started = self.clock() if started is None else started
        self.cancelled_exceptions = cancelled_exceptions
        self.rows = []
        self.dropped = 0

    def __enter__(self):
        self.token = _current.set(self)
        return self

    def __exit__(self, *unused):
        _current.reset(self.token)

    def snapshot(self, status):
        if status not in {'completed', 'failed', 'cancelled'}:
            raise ValueError('Unknown timing status.')
        return {
            'schema_version': 1, 'status': status,
            'worker_total_seconds': round(max(0, self.clock()-self.started), 6),
            'scope': 'Worker start through finalization; excludes queue and HTTP delivery.',
            'generation_metric_scope': 'Existing metrics.total_seconds ends before final quality summary and persistence.',
            'stage_durations_may_overlap': True,
            'stages': [dict(row) for row in self.rows], 'dropped_stages': self.dropped,
        }


@contextmanager
def stage(label):
    # Labels are a fixed code vocabulary, never user/model/source text.
    if label not in STAGES:
        raise ValueError('Unknown timing stage.')
    collector = _current.get()
    if collector is None:
        yield
        return
    if len(collector.rows) >= MAX_STAGES:
        collector.dropped += 1
        yield
        return
    started = collector.clock()
    row = {'stage': label, 'start_seconds': round(max(0, started-collector.started), 6),
           'seconds': 0.0, 'status': 'running'}
    collector.rows.append(row)
    try:
        yield
    except BaseException as error:
        row['status'] = 'cancelled' if isinstance(error, collector.cancelled_exceptions) else 'failed'
        raise
    else:
        row['status'] = 'completed'
    finally:
        row['seconds'] = round(max(0, collector.clock()-started), 6)


def timed(label, operation, *args, **kwargs):
    with stage(label):
        return operation(*args, **kwargs)
