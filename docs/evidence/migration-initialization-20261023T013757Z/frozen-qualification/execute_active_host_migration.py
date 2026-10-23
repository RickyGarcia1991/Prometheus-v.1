"""Explicit owner-profile migration entry, pending qualification. DO NOT AUTO-RUN.

No contract is created here. A separately reviewed normal-profile wrapper must
pin this file and provide the SHA256 of the fixed private execution contract.
Every attempt records durable private evidence; failures never trigger an
automatic rollback or repeat an ambiguous Start.
"""
import argparse,ctypes,hashlib,json,os,re,secrets,sys,traceback
from ctypes import wintypes as W
from pathlib import Path

BASE=Path(__file__).resolve().parent
CONTRACT=BASE/'production-migration-execution-contract.private.json'
REPORT=BASE/'active-migration-plan-profile-6a9a3021462849579adc46c903efda4e.private.json'
DEPENDENCIES=frozenset([
    'active_host_migration_core.py','active_host_migration_windows.py',
    'active_host_migration_windows_locks.py','active_migration_windows_locks.py',
    'active_host_migration_windows_io.py','active_host_migration_windows_store.py',
    'active_host_migration_windows_processes.py','active_host_migration_windows_images.py',
    'active_host_migration_windows_native.py','active_host_migration_windows_preservation.py',
    'active_host_migration_windows_provider.py','active_migration_preflight.py',
    'inspect_active_host_migration.py','check-signed-quiet-host-containment.owned-cleanup-frozen.py',
    'ActiveHostMigration.Native.psm1','collect-active-host-migration-preflight.ps1'])

class HeldSources:
    """Stdlib-only bootstrap: deny file write/delete through all module imports."""
    def __init__(self):
        self.handles=[];self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,W.LPVOID,W.DWORD,W.DWORD,W.HANDLE];self.k.CreateFileW.restype=W.HANDLE
        self.k.ReadFile.argtypes=[W.HANDLE,W.LPVOID,W.DWORD,ctypes.POINTER(W.DWORD),W.LPVOID];self.k.ReadFile.restype=W.BOOL
        self.k.GetFileSizeEx.argtypes=[W.HANDLE,ctypes.POINTER(ctypes.c_longlong)];self.k.GetFileSizeEx.restype=W.BOOL
        self.k.GetFinalPathNameByHandleW.argtypes=[W.HANDLE,W.LPWSTR,W.DWORD,W.DWORD];self.k.GetFinalPathNameByHandleW.restype=W.DWORD
        self.k.CloseHandle.argtypes=[W.HANDLE];self.k.CloseHandle.restype=W.BOOL
    def read(self,path,pin):
        if not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin):raise ValueError('Exact SHA256 required')
        path=Path(path).absolute()
        for item in (path,*path.parents):
            if item.is_symlink() or getattr(item.stat(),'st_file_attributes',0)&0x400:raise ValueError('Source reparse refused')
        handle=self.k.CreateFileW(str(path),0x80000000,1,None,3,0x80,None)
        if handle in (None,ctypes.c_void_p(-1).value):raise ctypes.WinError(ctypes.get_last_error())
        self.handles.append(handle)
        final=ctypes.create_unicode_buffer(32768);length=self.k.GetFinalPathNameByHandleW(handle,final,len(final),0)
        if not length or length>=len(final) or os.path.normcase(final.value)!=os.path.normcase('\\\\?\\'+str(path)):raise ValueError('Source physical path changed')
        size=ctypes.c_longlong()
        if not self.k.GetFileSizeEx(handle,ctypes.byref(size)) or not 0<=size.value<=4194304:raise ValueError('Source size bound')
        data=ctypes.create_string_buffer(size.value+1);count=W.DWORD()
        if not self.k.ReadFile(handle,data,len(data),ctypes.byref(count),None) or count.value!=size.value:raise ValueError('Source read failed')
        raw=bytes(data.raw[:count.value])
        if hashlib.sha256(raw).hexdigest()!=pin:raise ValueError('Reviewed source pin mismatch: '+path.name)
        return raw
    def close(self):
        errors=[];remaining=[]
        for handle in self.handles:
            try:
                if not self.k.CloseHandle(handle):raise RuntimeError('Held source close failed')
            except BaseException as error:errors.append(repr(error));remaining.append(handle)
        self.handles=remaining
        if errors:raise RuntimeError(repr(errors))

def durable_new(path,value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    with open(path,'xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if path.read_bytes()!=raw:raise RuntimeError('Attempt evidence readback failed')

def validate_contract(contract,operation):
    if type(contract) is not dict or set(contract)!={'schema','plan_report_sha256','transaction','source_pins','operations'}:raise ValueError('Execution contract schema')
    if type(contract['schema']) is not int or contract['schema']!=1 or contract['operations']!=['initialize','resume','rollback'] or operation not in contract['operations']:raise ValueError('Explicit reviewed operation required')
    if not isinstance(contract['transaction'],str) or not re.fullmatch('[0-9a-f]{32}',contract['transaction']):raise ValueError('Transaction binding')
    if type(contract['source_pins']) is not dict or set(contract['source_pins'])!=DEPENDENCIES:raise ValueError('Exact dependency closure required')
    for pin in [contract['plan_report_sha256'],*contract['source_pins'].values()]:
        if not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin):raise ValueError('Malformed source pin')

def run_bound(plan,operation,module_pin):
    # Imports happen only after the bootstrap has retained every reviewed source.
    import active_host_migration_windows as policy
    from active_host_migration_windows_provider import ProductionProvider
    from active_host_migration_windows_native import capture_observation_pair
    from inspect_active_host_migration import sessions_snapshot
    context,_=policy.validate_production_plan(plan)
    directory=Path(context['root'])/'ActiveHostMigration'/plan['transaction']
    if operation=='initialize' and directory.exists():raise ValueError('Initialize refuses existing transaction; explicit resume required')
    if operation!='initialize' and not directory.exists():raise ValueError('Recovery requires existing transaction')

    class InitialBoundaryProvider(ProductionProvider):
        def authority(self,current):
            result=super().authority(current)
            if operation=='initialize' and not hasattr(self,'store'):
                # Called again by store.unchanged under its actual outer lock,
                # before any transaction directory/key creation. Original plan
                # identities are never refreshed or silently adopted.
                pair=capture_observation_pair(self.latest,self.images) if current['original_running'] else {}
                fresh=policy.build_plan(self.latest,sessions_snapshot(),pair,current['transaction'])
                if fresh!=current:raise ValueError('Reviewed initial plan changed before transaction creation')
            return result

    provider=None;failure=None;result=None;cleanup=[]
    try:
        provider=InitialBoundaryProvider(plan,module_pin,initialize=operation=='initialize',mode='rollback' if operation=='rollback' else 'forward')
        result=provider.rollback() if operation=='rollback' else provider.run()
    except BaseException:
        failure=traceback.format_exc()
    finally:
        if provider is not None:
            try:provider.close()
            except BaseException as error:cleanup.append(repr(error))
    return dict(operation=operation,result=result,error=failure,cleanup_errors=cleanup,passed=failure is None and not cleanup)

def execute(operation,contract_sha256):
    if os.name!='nt' or not sys.dont_write_bytecode:raise ValueError('Windows Python -B required')
    # -B suppresses writes but still permits reading existing pyc. Use a fresh,
    # absent cache namespace so every reviewed local module loads held source.
    cache=BASE/('execution-unused-bytecode-'+secrets.token_hex(16))
    if cache.exists():raise ValueError('Unexpected bytecode namespace')
    sys.pycache_prefix=str(cache)
    held=HeldSources();attempt=secrets.token_hex(16);out=None;cleanup=[]
    intent=BASE/f'production-migration-attempt-{attempt}.intent.private.json'
    result_path=BASE/f'production-migration-attempt-{attempt}.result.private.json'
    try:
        contract=json.loads(held.read(CONTRACT,contract_sha256));validate_contract(contract,operation)
        for name,pin in contract['source_pins'].items():held.read(BASE/name,pin)
        report=json.loads(held.read(REPORT,contract['plan_report_sha256']));plan=report['inspection']['capture']['plan']
        if plan['transaction']!=contract['transaction']:raise ValueError('Concrete plan transaction differs')
        durable_new(intent,dict(schema=1,attempt=attempt,operation=operation,contract_sha256=contract_sha256,plan_report_sha256=contract['plan_report_sha256'],transaction=plan['transaction']))
        out=run_bound(plan,operation,contract['source_pins']['ActiveHostMigration.Native.psm1'])
    except BaseException:
        out=dict(passed=False,error=traceback.format_exc(),operation=operation)
    finally:
        try:held.close()
        except BaseException as error:cleanup.append(repr(error))
    out.update(attempt=attempt,source_cleanup_errors=cleanup)
    if cleanup:out['passed']=False
    durable_new(result_path,out)
    print(json.dumps(dict(passed=out['passed'],attempt=attempt,private_result=str(result_path)),sort_keys=True))
    return 0 if out['passed'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['initialize','resume','rollback']);parser.add_argument('--contract-sha256',required=True)
    args=parser.parse_args();raise SystemExit(execute(args.operation,args.contract_sha256))
