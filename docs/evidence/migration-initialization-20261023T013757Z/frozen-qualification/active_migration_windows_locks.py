"""Windows migration mutex primitive: GENERATED FIXTURES ONLY, no production adapter.

Session-local locks do not exclude competing logon sessions. This module refuses
unsupported session observations and never claims production cross-session exclusion.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass
import os
import re
import threading

OUTER = ('ReconnectEnrollment',) + tuple('Eject-' + chr(x) for x in range(65, 91))
RUNTIME = ('UsbLifecycle', 'Arrival', 'HostAgent')
PREFIX = 'Local\\PrometheusMigrationFixture-'

class LockRefusal(RuntimeError): pass
class LockBusy(LockRefusal): pass
class RevalidationRequired(LockRefusal): pass

@dataclass(frozen=True)
class SessionContext:
    caller_session: int
    task_session: int
    observed_worker_sessions: tuple
    fixture_only: bool = True

class Native:
    def __init__(self):
        if os.name != 'nt':
            raise LockRefusal('Windows only')
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        def fn(name,args,result):
            value=getattr(self.k,name);value.argtypes=args;value.restype=result;return value
        self.create=fn('CreateMutexW',[W.LPVOID,W.BOOL,W.LPCWSTR],W.HANDLE)
        self.wait=fn('WaitForSingleObject',[W.HANDLE,W.DWORD],W.DWORD)
        self.release=fn('ReleaseMutex',[W.HANDLE],W.BOOL)
        self.close=fn('CloseHandle',[W.HANDLE],W.BOOL)
        self.session=fn('ProcessIdToSessionId',[W.DWORD,ctypes.POINTER(W.DWORD)],W.BOOL)
        self.thread=fn('GetCurrentThreadId',[],W.DWORD)
    def session_id(self):
        value=W.DWORD()
        if not self.session(os.getpid(),ctypes.byref(value)):raise ctypes.WinError(ctypes.get_last_error())
        return value.value
    def open(self,name):
        if not re.fullmatch(r'Local\\PrometheusMigrationFixture-[a-f0-9]{32}-(?:ReconnectEnrollment|Eject-[A-Z]|UsbLifecycle|Arrival|HostAgent)',name):
            raise LockRefusal('Only generated fixture mutex names allowed')
        handle=self.create(None,False,name)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        return handle

class WindowsMigrationLocks:
    """One native owner thread, explicitly ordered groups, nonblocking acquisition.

    A revalidator receives the abandoned role names, and must return literal True.
    This acknowledges reinspection of protected state, never automatic recovery.
    All callbacks run synchronously on the owning thread while handles are held.
    """
    production_activation_available = False
    def __init__(self, namespace, context, *, session_probe=None):
        if not isinstance(namespace,str) or not re.fullmatch('[a-f0-9]{32}',namespace):
            raise LockRefusal('Fresh generated namespace token required')
        self.api=Native();self.namespace=namespace;self.context=context
        self._thread=(threading.get_ident(),self.api.thread())
        self._probe=session_probe or (lambda: context)
        self._held=[];self.unresolved=[];self.poisoned=False;self._outer=False;self._runtime=False;self._acquiring=False
        self.events=[]
        self._check()
    def name(self,role):
        if role not in OUTER+RUNTIME:raise LockRefusal('Unknown mutex role')
        return PREFIX+self.namespace+'-'+role
    def _check(self):
        if self._thread!=(threading.get_ident(),self.api.thread()):
            raise LockRefusal('Native mutex ownership is thread-affine')
        if self.poisoned:raise LockRefusal('Native cleanup uncertainty; owner permanently poisoned')
        observed=self._probe()
        if not isinstance(observed,SessionContext) or observed!=self.context:
            raise LockRefusal('Session observations changed')
        actual=self.api.session_id()
        if (observed.fixture_only is not True or type(observed.caller_session) is not int
            or type(observed.task_session) is not int or observed.caller_session!=actual
            or observed.task_session!=actual or actual==0
            or not isinstance(observed.observed_worker_sessions,tuple)
            or any(type(x) is not int or x!=actual for x in observed.observed_worker_sessions)):
            raise LockRefusal('Unsupported session context; production exclusion unavailable')
    def _close_unowned(self,role,handle):
        try:
            if not self.api.close(handle):raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as exc:
            self.poisoned=True
            self.unresolved.append(dict(role=role,handle=handle,owned=False,close_error=repr(exc)))
            raise LockRefusal('Unowned handle close failed; owner poisoned') from exc
    def _release(self,acquired):
        # Never transfer native ownership to another thread. Failed release and
        # close attempts are recorded independently; no uncertain owner is reused.
        if self._thread!=(threading.get_ident(),self.api.thread()):
            self.poisoned=True
            raise LockRefusal('Cannot release mutex from another thread')
        errors=[]
        for role,handle in reversed(acquired):
            release_error=None;close_error=None
            try:
                if not self.api.release(handle):raise ctypes.WinError(ctypes.get_last_error())
                self.events.append(('release',role))
            except BaseException as exc:
                release_error=repr(exc)
            try:
                if not self.api.close(handle):raise ctypes.WinError(ctypes.get_last_error())
            except BaseException as exc:
                close_error=repr(exc)
            if release_error or close_error:
                self.poisoned=True
                record=dict(role=role,handle=handle,owned=bool(release_error),
                            release_error=release_error,close_error=close_error,
                            handle_closed=close_error is None)
                self.unresolved.append(record);errors.append(record)
            else:
                self._held.remove((role,handle))
        if errors:raise LockRefusal('Independent release/close failures; owner poisoned: '+repr(errors))
    @contextmanager
    def _group(self,roles,revalidate):
        self._check()
        if self._acquiring:raise LockRefusal('Recursive acquisition refused')
        self._acquiring=True;acquired=[];abandoned=[]
        try:
            for role in roles:
                self._check()
                handle=self.api.open(self.name(role))
                try:
                    status=self.api.wait(handle,0)
                except BaseException:
                    # Native wait exceptions have no trustworthy ownership result.
                    self.poisoned=True
                    self.unresolved.append(dict(role=role,handle=handle,owned='unknown',wait_error=True))
                    self._close_unowned(role,handle)
                    raise
                if status not in (0,0x80):
                    self._close_unowned(role,handle)
                    if status==0x102:raise LockBusy(role)
                    self.poisoned=True
                    raise LockRefusal('Native wait failed: '+str(status))
                acquired.append((role,handle));self._held.append((role,handle))
                self.events.append(('acquire',role))
                if status==0x80:abandoned.append(role)
            self._check()
            if abandoned:
                self.events.append(('abandoned',tuple(abandoned)))
                if revalidate is None or revalidate(tuple(abandoned)) is not True:
                    raise RevalidationRequired('Abandoned acquisition requires explicit state revalidation')
                self._check();self.events.append(('revalidated',tuple(abandoned)))
            self._acquiring=False
            yield
        finally:
            self._acquiring=False
            self._release(acquired)
    @contextmanager
    def outer_lock(self, revalidate=None):
        self._check()
        if self._outer or self._runtime or self._held:raise LockRefusal('Outer lock reentry/order refused')
        with self._group(OUTER,revalidate):
            self._outer=True
            try:yield self
            finally:self._outer=False
    @contextmanager
    def quiescent(self, revalidate=None):
        self._check()
        if not self._outer or self._runtime:raise LockRefusal('Runtime requires outer; no reentry')
        with self._group(RUNTIME,revalidate):
            self._runtime=True
            try:yield self
            finally:self._runtime=False
    def assert_restart_allowed(self):
        self._check()
        if not self._outer or self._runtime or self._acquiring or any(role in RUNTIME for role,_ in self._held):
            raise LockRefusal('Runtime locks must release before restart; outer lock required')
        if tuple(role for role,_ in self._held)!=OUTER:raise LockRefusal('Incomplete outer lock ownership')
        return True
