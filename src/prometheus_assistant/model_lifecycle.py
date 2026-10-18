"""Process-local model admission and cooperative cancellation.

Wrap work in task_scope with a fresh mode provider, then hold governor.lease
across the entire HTTP operation (including response close). Call lease.check
while polling I/O. Maintenance leases only serialize explicit model unloads;
they do not authorize inference. Context variables do not propagate to newly
created threads: establish task_scope in the worker itself. No process is killed.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re
import threading
import time


class LifecycleError(RuntimeError):
    pass


class LifecycleBlocked(LifecycleError):
    pass


class LifecycleBusy(LifecycleError):
    pass


class LifecycleCooldown(LifecycleError):
    pass


class LifecycleCancelled(LifecycleError):
    pass


@dataclass(frozen=True)
class _Scope:
    mode_provider: object = None
    optional: bool = False
    cancel_events: tuple = ()


_scope = ContextVar('model_lifecycle_scope', default=_Scope())


@contextmanager
def task_scope(*, mode_provider=None, optional=None, cancel_event=None):
    """Scope a worker; nested optional work retains its enclosing authority."""
    parent = _scope.get()
    events = parent.cancel_events + ((cancel_event,) if cancel_event is not None else ())
    token = _scope.set(_Scope(mode_provider if mode_provider is not None else parent.mode_provider,
                              parent.optional or bool(optional), events))
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def optional_scope():
    with task_scope(optional=True):
        yield


def _mode(scope):
    if scope.mode_provider is None:
        return 'balanced'
    try:
        value = scope.mode_provider()
    except Exception as exc:
        raise LifecycleBlocked('Runtime mode could not be checked.') from exc
    if not isinstance(value, str):
        raise LifecycleBlocked('Unknown runtime mode blocks model inference.')
    value = ' '.join(value.lower().replace('_', ' ').replace('-', ' ').split())
    if value not in {'balanced', 'performance', 'idle', 'battery saver', 'critical recovery'}:
        raise LifecycleBlocked('Unknown runtime mode blocks model inference.')
    return value


class _Lease:
    def __init__(self, scope, cancel_event, maintenance):
        self.scope = scope
        self.cancel_event = cancel_event
        self.maintenance = maintenance

    def check(self):
        # Explicit unload remains available after its parent task is cancelled.
        if self.maintenance:
            return
        for event in self.scope.cancel_events + ((self.cancel_event,) if self.cancel_event is not None else ()):
            if event.is_set():
                raise LifecycleCancelled('Model operation cancelled.')
        if not self.maintenance:
            mode = _mode(self.scope)
            if mode == 'critical recovery' or (self.scope.optional and mode in {'idle', 'battery saver'}):
                raise LifecycleBlocked('Runtime mode blocks this model operation.')

    def keep_alive(self, default_seconds=120):
        """Bound residency to 15 minutes; optional/restrictive work unloads."""
        if self.maintenance:
            return 0
        mode = _mode(self.scope)
        if self.scope.optional or mode in {'idle', 'battery saver', 'critical recovery'}:
            return 0
        if isinstance(default_seconds, str):
            match = re.fullmatch(r'(\d+)(s|m|h)?', default_seconds.strip())
            if match:
                seconds = int(match[1]) * {'s': 1, 'm': 60, 'h': 3600, None: 1}[match[2]]
                return default_seconds if seconds <= 900 else 900
            return 120
        if type(default_seconds) in (int, float):
            return max(0, min(900, int(default_seconds)))
        return 120


class ModelLifecycleGovernor:
    """One fail-fast inference lease and bounded exact-configuration cooldowns."""
    def __init__(self, *, clock=time.monotonic, cooldown_seconds=30, max_failures=128):
        if not 0 <= cooldown_seconds <= 300 or not 1 <= max_failures <= 4096:
            raise ValueError('Lifecycle cooldown or entry bound is invalid.')
        self._clock = clock
        self._cooldown_seconds = cooldown_seconds
        self._max_failures = max_failures
        self._lock = threading.Lock()
        self._active = False
        self._failures = {}

    @contextmanager
    def lease(self, endpoint, model, num_ctx, *, cancel_event=None, maintenance=False):
        key = (endpoint, model, num_ctx)
        lease = _Lease(_scope.get(), cancel_event, maintenance)
        lease.check()
        with self._lock:
            now = self._clock()
            self._failures = {k: deadline for k, deadline in self._failures.items() if deadline > now}
            if self._active:
                raise LifecycleBusy('Another local model operation is already running.')
            if not maintenance and key in self._failures:
                raise LifecycleCooldown('This model configuration is cooling down after a failure.')
            self._active = True
        try:
            lease.check()
            yield lease
        except LifecycleError:
            raise
        except Exception:
            if not maintenance:
                with self._lock:
                    self._failures[key] = self._clock() + self._cooldown_seconds
                    while len(self._failures) > self._max_failures:
                        del self._failures[next(iter(self._failures))]
            raise
        finally:
            with self._lock:
                self._active = False


governor = ModelLifecycleGovernor()



