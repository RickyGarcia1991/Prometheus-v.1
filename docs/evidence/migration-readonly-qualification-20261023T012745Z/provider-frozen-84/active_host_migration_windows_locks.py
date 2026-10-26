"""Explicit production name/context policy over the unchanged qualified owner.

No top-level acquisition or launcher. Tests inject a fake API and never acquire
production names. The original generated-only class and its guards are unchanged.
"""
import ctypes,hashlib,re,threading
from dataclasses import dataclass
from pathlib import Path
import active_migration_windows_locks as engine
from active_host_migration_windows import production_mutex_name

ENGINE_PIN='50f178ce72f162b7760470209fc27bb3fc3748184574b30e81abc92ef8bcba45'
if hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest()!=ENGINE_PIN:raise RuntimeError('Qualified owner engine changed')


@dataclass(frozen=True)
class ProductionSession:
    sid:str
    session_id:int
    task_sid:str
    task_session:int
    eligible_session_ids:tuple
    worker_session_ids:tuple
    complete:bool


class ProductionNative(engine.Native):
    def open(self,name):
        allowed={production_mutex_name(role) for role in engine.OUTER+engine.RUNTIME}
        if name not in allowed:raise engine.LockRefusal('Fixed production mutex names only')
        handle=self.create(None,False,name)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        return handle


class ProductionMigrationLocks(engine.WindowsMigrationLocks):
    """Shared owner algorithm, explicit fresh cooperative owner/session policy."""
    production_activation_available=False
    def __init__(self,context,*,session_probe,api=None):
        if not callable(session_probe):raise engine.LockRefusal('Fresh session probe required')
        self.api=api if api is not None else ProductionNative()
        self.context=context;self._probe=session_probe
        self._thread=(threading.get_ident(),self.api.thread())
        self._held=[];self.unresolved=[];self.poisoned=False
        self._outer=False;self._runtime=False;self._acquiring=False;self.events=[]
        self._check()

    def name(self,role):
        if role not in engine.OUTER+engine.RUNTIME:raise engine.LockRefusal('Unknown role')
        return production_mutex_name(role)

    def _check(self):
        if self._thread!=(threading.get_ident(),self.api.thread()):raise engine.LockRefusal('Native mutex ownership is thread-affine')
        if self.poisoned:raise engine.LockRefusal('Native cleanup uncertainty; owner poisoned')
        observed=self._probe()
        if type(observed) is not ProductionSession or observed!=self.context:raise engine.LockRefusal('Fresh production session changed')
        current=self.api.session_id()
        if (type(observed.complete) is not bool or not observed.complete
            or type(observed.session_id) is not int or observed.session_id<=0
            or observed.session_id!=current or type(observed.task_session) is not int or observed.task_session!=current
            or not isinstance(observed.sid,str) or not re.fullmatch(r'S-1-\d+(?:-\d+)+',observed.sid)
            or observed.task_sid!=observed.sid
            or type(observed.eligible_session_ids) is not tuple or any(type(value) is not int for value in observed.eligible_session_ids) or observed.eligible_session_ids!=(current,)
            or type(observed.worker_session_ids) is not tuple
            or any(type(value) is not int or value!=current for value in observed.worker_session_ids)):
            raise engine.LockRefusal('Unsupported cooperative single-owner-session context')
