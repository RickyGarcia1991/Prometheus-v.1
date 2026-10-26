"""Query-only retained identities for independent application/controller workers.

No termination rights are requested and no stop operation exists. Baseline records
are private authenticated transaction evidence, not publication material.
"""
import os
from active_host_migration_windows_processes import q,command_snapshot

class IndependentPreservation:
    def __init__(self,store,native,*,initialize=False,api=q,snapshot=command_snapshot):
        self.store=store;self.native=native;self.api=api;self.snapshot=snapshot;self.handles={};self.unresolved=[]
        path=store.directory/'independent-baseline.json'
        try:
            with store.operation():
                if path.exists():baseline=store.record(path.name,'independent-baseline')
                else:
                    if not initialize or store.journal.exists():raise ValueError('Missing original preservation baseline; no retrospective creation')
                    observed=native.call('Independents');rows=[]
                    if type(observed) is not dict or set(observed)!={'pids','ports'} or type(observed['pids']) is not list or len(observed['pids'])>64 or len(set(observed['pids']))!=len(observed['pids']):raise ValueError('Independent inventory schema')
                    for pid in observed['pids']:rows.append(self.capture(pid))
                    baseline=dict(rows=rows,ports=observed['ports'])
                    store.files.create(path,store.seal_record('independent-baseline',baseline))
                if type(baseline) is not dict or set(baseline)!={'rows','ports'} or type(baseline['rows']) is not list or len(baseline['rows'])>64:raise ValueError('Independent baseline schema')
                self.baseline=baseline
                for row in baseline['rows']:
                    if type(row) is not dict or set(row)!={'pid','identity','command'}:raise ValueError('Independent row schema')
                    if row['pid'] not in self.handles:
                        if self.capture(row['pid'])!=row:raise ValueError('Independent original process replaced')
                self.assert_alive()
        except BaseException:
            self.close()
            raise

    def capture(self,pid):
        if type(pid) is not int or pid<=0 or pid in self.handles:raise ValueError('Independent PID scope/duplicate')
        handle=self.api.Open(0x100000|0x1000,False,pid);self.api.require(handle)
        self.handles[pid]=handle
        identity=self.api.identity(handle);command=self.snapshot(pid)
        if identity['owner']!=self.store.files.sid or self.api.Wait(handle,0)!=258 or self.api.identity(handle)!=identity:raise ValueError('Independent owner/identity changed')
        return dict(pid=pid,identity=identity,command=command)

    def assert_alive(self):
        if self.unresolved:raise ValueError('Independent handle cleanup uncertainty')
        for row in self.baseline['rows']:
            handle=self.handles[row['pid']]
            if self.api.Wait(handle,0)!=258 or self.api.identity(handle)!=row['identity'] or self.snapshot(row['pid'])!=row['command']:raise ValueError('Independent process not preserved')
        observed=self.native.call('Independents')
        original=self.baseline['ports']
        if type(original) is not list or len(original)>128:raise ValueError('Port baseline bound')
        for port in original:
            if type(port) is not dict or set(port)!={'port','pid'} or type(port['port']) is not int or port['port'] not in (54555,54559) or type(port['pid']) is not int or port['pid'] not in self.handles:raise ValueError('Port ownership schema')
            if port not in observed['ports']:raise ValueError('Independent listener not preserved')
        return True

    def close(self):
        errors=[]
        for pid,handle in list(self.handles.items()):
            try:
                if not self.api.Close(handle):raise RuntimeError('CloseHandle failed')
            except BaseException as error:errors.append(dict(pid=pid,handle=handle,error=repr(error)))
            finally:del self.handles[pid]
        self.unresolved.extend(errors)
        if errors:raise RuntimeError('Independent preservation handle cleanup uncertain')
