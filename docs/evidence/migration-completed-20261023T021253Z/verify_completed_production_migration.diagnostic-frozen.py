"""Normal-profile authenticated completed-chain inspection, mutation APIs denied."""
import hashlib,json,sys,secrets
from pathlib import Path
BASE=Path(__file__).resolve().parent
_cache=BASE/('verification-unused-bytecode-'+secrets.token_hex(16))
if _cache.exists():raise ValueError('Unexpected bytecode namespace')
sys.pycache_prefix=str(_cache)
# The reviewed normal-profile wrapper retains this bootstrap file with writes
# and deletion denied across the whole child execution, before this import.
if hashlib.sha256((BASE/'execute_active_host_migration.py').read_bytes()).hexdigest()!='e1236d70d83b3b1b6bf200d59d77aa5c2bed8c40543853ba6d4e5926faaa52a0':raise ValueError('Reviewed bootstrap changed')
from execute_active_host_migration import HeldSources,CONTRACT,REPORT,validate_contract
CONTRACT_PIN='50b5b2e89039f9a41f9805ce9d6d324bd5da4afda9109ccd07c60abd47dd216b'
EARLIER=BASE/'active-migration-normal-profile-11179eac49824cc28140561a98a8c4e3.private.json'
EARLIER_PIN='157cd05429f7c78e9bd2f8c4c2caf17f60131693434c2c4422532eac4b5bd39a'

def inspect():
    if not sys.dont_write_bytecode:raise ValueError('Python -B required')
    held=HeldSources();provider=None;errors=[];result=None
    try:
        contract=json.loads(held.read(CONTRACT,CONTRACT_PIN));validate_contract(contract,'resume')
        for name,pin in contract['source_pins'].items():held.read(BASE/name,pin)
        plan=json.loads(held.read(REPORT,contract['plan_report_sha256']))['inspection']['capture']['plan']
        earlier=json.loads(held.read(EARLIER,EARLIER_PIN))['inspection']['evidence']['observed_workers']
        import active_host_migration_core as core
        import active_host_migration_windows_provider as implementation
        from active_host_migration_windows_io import PhysicalFiles
        from active_host_migration_windows_native import NativeOperations
        from active_host_migration_windows_processes import q
        def deny(*args,**kwargs):raise RuntimeError('Read-only verifier denied mutation')
        class ReadOnlyFiles(PhysicalFiles):
            create=mkdir=replace=publish_new=deny
            def handle(self,path,*,directory=False,create=False):
                if create:deny()
                return super().handle(path,directory=directory,create=False)
        class ReadOnlyNative(NativeOperations):
            def call(self,operation='Observe',*args,**kwargs):
                if operation not in ('Observe','Independents'):deny()
                return super().call(operation,*args,**kwargs)
        implementation.PhysicalFiles=ReadOnlyFiles
        implementation.NativeOperations=ReadOnlyNative
        q.Terminate=deny
        provider=implementation.ProductionProvider(plan,contract['source_pins']['ActiveHostMigration.Native.psm1'],initialize=False)
        with provider.outer_lock():
            chain=core.verify_completed_chain(provider,plan,provider.key)
            store=provider.store;store.verify_backup();store.verify_replacement()
            receipt=store.record('start-receipt.json','start-receipt')
            previous={row['kind']:row for row in earlier if row['kind'] in ('wrapper','supervisor')}
            if set(previous)!={'wrapper','supervisor'}:raise ValueError('Earlier first-start pair evidence')
            fields=('pid','parent_pid','creation_ticks','sid','session_id','image','image_sha256')
            differences=[dict(role=role,field=field,earlier=previous[role][field],current=row[field],earlier_type=type(previous[role][field]).__name__,current_type=type(row[field]).__name__) for role,row in receipt['pair'].items() for field in fields if row[field]!=previous[role][field]]
            if differences:raise ValueError('Current pair differs from first-start observation: '+json.dumps(differences,sort_keys=True))
            backup=store.owned('backup',store.intent('backup',False))
            paths={'authorization':store.auth,'original_enrollment':store.original,'old_payload_backup':backup,'current_payload':store.target,'protected_key':store.keyfile,'migration_journal':store.journal}
            hashes={name:hashlib.sha256(store.files.read(path,16777216)).hexdigest() for name,path in paths.items()}
            if store.files.read(store.keyfile,65536)!=store.protected_key_bytes:raise ValueError('Key changed during verification')
            result=dict(inspection_only=True,mutation_apis_denied=True,status='authenticated_completed_chain_verified',production_provider_used=True,legacy_core_live_adapter_flag_is_not_execution_identity=True,chain=chain,hashes=hashes,original_authorization_unchanged=hashes['authorization']==plan['auth_sha256'],original_enrollment_unchanged=hashes['original_enrollment']==plan['enrollment_sha256'],old_backup_exact=hashes['old_payload_backup']==plan['old_file_sha256'],new_payload_exact=hashes['current_payload']==plan['new_file_sha256'],protected_key_stable_during_verification=True,historical_key_hash_comparison_performed=False,same_pair_as_first_start_observation=True,earlier_observation_sha256=EARLIER_PIN,start_intent=receipt['intent'],current_pair=receipt['pair'],task_state=provider.latest['observation']['task']['state'],observed_worker_count=len(provider.latest['observation']['workers']),independent_processes_preserved=True,no_second_start_basis='Same native pair as first-start observation; reviewed resume branch skips Start when that pair exists. No separate Scheduler invocation audit is asserted.')
    finally:
        if provider is not None:
            try:provider.close()
            except BaseException as error:errors.append(repr(error))
        try:held.close()
        except BaseException as error:errors.append(repr(error))
        if errors:raise RuntimeError('Verification cleanup uncertainty: '+repr(errors))
    return result

if __name__=='__main__':print(json.dumps(inspect(),sort_keys=True))
