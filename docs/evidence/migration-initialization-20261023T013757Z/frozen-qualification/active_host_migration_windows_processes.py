"""Retained native owner for the exact signed task wrapper/immediate child.

No process enumeration or mutation runs at import. No descendant termination API.
The provider supplies a validated plan and freshly captured command/file evidence.
"""
import ctypes,hashlib,importlib.util,json,os
from pathlib import Path
import active_host_migration_windows as policy

BASE=Path(__file__).resolve().parent
HELPER=BASE/'check-signed-quiet-host-containment.owned-cleanup-frozen.py'
if hashlib.sha256(HELPER.read_bytes()).hexdigest()!='d6cca2a4b4cb7b12e0f100ca0c36abd0ab0160fed3216e3273e0cc860dc8bbde':raise RuntimeError('Qualified native process primitives changed')
spec=importlib.util.spec_from_file_location('production_process_primitives',HELPER)
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
DOTNET_FILETIME_DELTA=504911232000000000


def command_snapshot(pid):
    if type(pid) is not int or pid<=0:raise ValueError('PID')
    return q.ps_json("$p=Get-CimInstance Win32_Process -Filter 'ProcessId="+str(pid)+"'; if(!$p){throw 'Captured process absent'}; @{pid=[long]$p.ProcessId;parent_pid=[long]$p.ParentProcessId;session_id=[int]$p.SessionId;command_line=[string]$p.CommandLine}|ConvertTo-Json -Compress")


class CapturedPair:
    def __init__(self,plan,*,snapshot=command_snapshot,api=q,file_probe=None):
        policy.validate_production_plan(plan)
        if not plan['original_running']:raise ValueError('Stopped plan has no pair to capture')
        if not callable(file_probe):raise ValueError('Physical image/file identity probe required')
        self.api=api;self.snapshot=snapshot;self.file_probe=file_probe;self.handles={};self.unresolved=[];self.poisoned=False
        self.rows={role:json.loads(plan[field]) for role,field in (('wrapper','wrapper_identity'),('child','child_identity'))}
        try:
            for role,row in self.rows.items():
                handle=api.Open(0x100000|0x1000|(1 if role=='wrapper' else 0),False,row['pid'])
                api.require(handle)
                # Track immediately so partial/failed validation cannot lose it.
                self.handles[role]=handle
                self.verify(role)
        except BaseException:
            self.close()
            raise

    def verify(self,role,allow_exited=False):
        if self.poisoned:raise RuntimeError('Process owner poisoned')
        row=self.rows[role];handle=self.handles[role];status=self.api.Wait(handle,0)
        if status==0 and allow_exited:return False
        if status!=258:raise ValueError('Captured process exited/unknown wait')
        actual=self.api.identity(handle)
        if int(actual['created'])+DOTNET_FILETIME_DELTA!=row['creation_ticks'] or actual['owner']!=row['sid'] or os.path.normcase(actual['image'])!=os.path.normcase(row['image']):raise ValueError('Retained process creation/image/owner changed')
        observed=self.snapshot(row['pid'])
        if observed!={key:row[key] for key in ('pid','parent_pid','session_id','command_line')}:raise ValueError('Exact process command/parent/session changed')
        physical=self.file_probe(row['image'])
        if physical!={'sha256':row['image_sha256'],'file_identity':row['file_identity']}:raise ValueError('Physical image hash/file identity changed')
        if self.api.Wait(handle,0)!=258 or self.api.identity(handle)!=actual:raise ValueError('Process changed during verification')
        return True

    def stop_wrapper(self,timeout_ms=10000):
        if type(timeout_ms) is not int or not 1<=timeout_ms<=30000:raise ValueError('Bounded stop timeout')
        wrapper_alive=self.verify('wrapper',True)
        self.verify('child',True)
        if wrapper_alive:
            if not self.api.Terminate(self.handles['wrapper'],0) and self.api.Wait(self.handles['wrapper'],0)!=0:self.api.require(False)
        for role in ('wrapper','child'):
            if self.api.Wait(self.handles[role],timeout_ms)!=0:raise TimeoutError('Owned '+role+' did not exit; no descendant fallback')

    def close(self):
        failures=[]
        for role,handle in list(self.handles.items()):
            try:
                if not self.api.Close(handle):raise RuntimeError('CloseHandle failed')
            except BaseException as error:
                self.poisoned=True;record=dict(role=role,handle=handle,error=repr(error));self.unresolved.append(record);failures.append(record)
            finally:del self.handles[role]
        if failures:raise RuntimeError('Native process ownership cleanup uncertain: '+repr(failures))
