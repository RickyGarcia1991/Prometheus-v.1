"""Production callback composition under review. No command-line activation.

Only explicit construction/run can mutate. A reviewed module pin, validated plan,
physical owner context and verified original enrollment are mandatory. The live
launcher remains absent pending native integration and rollback review.
"""
from contextlib import ExitStack,contextmanager
import ctypes,hashlib,json,secrets,time
from pathlib import Path
import active_host_migration_core as core
import active_host_migration_windows as policy
from active_host_migration_windows_io import PhysicalFiles
from active_host_migration_windows_store import ProductionMigrationStore
from active_host_migration_windows_locks import ProductionMigrationLocks,ProductionSession
from active_host_migration_windows_processes import CapturedPair,q,DOTNET_FILETIME_DELTA
from active_host_migration_windows_images import ImagePins
from active_host_migration_windows_native import NativeOperations,capture_observation_pair
from active_host_migration_windows_preservation import IndependentPreservation
from inspect_active_host_migration import sessions_snapshot

BASE=Path(__file__).resolve().parent

class ProductionProvider:
    production_activation_available=False
    def __init__(self,plan,reviewed_module_sha256,*,initialize=False,mode='forward',fault=None,initial_guard=None,resume_initialization=False):
        if (initialize or resume_initialization) and not callable(initial_guard):raise ValueError('Fresh exact initial boundary guard required')
        self.plan=json.loads(core.encode(plan));self.context,self.binding=policy.validate_production_plan(self.plan)
        self.native=NativeOperations(reviewed_module_sha256);self.mode=mode;self.fault=fault or (lambda point:None)
        self.images=ImagePins();self.package_handles=ExitStack();self.pairs=[];self.original_pair=None;self.preservation=None;self.closed=False
        try:
            self.authority(self.plan)
            self.locks=ProductionMigrationLocks(self.session_context(),session_probe=self.session_context)
            self.files=PhysicalFiles(self.context['root'],self.context['sid'],administrative_authorization_owner=True)
            packages=[]
            for release in ('r5','r7'):
                folder=BASE/('reconnect-enrollment-candidate-'+release);files=PhysicalFiles(folder,self.context['sid'])
                manifest=json.loads(files.read(folder/'SHA256-MANIFEST.json'))
                if len(manifest)!=7:raise ValueError('Exact signed package size')
                for row in manifest:
                    if '/' in row['path'] or '\\' in row['path']:raise ValueError('Package entry scope')
                    self.package_handles.enter_context(files.handle(folder/row['path']))
                    if hashlib.sha256(files.read(folder/row['path'],16777216)).hexdigest()!=row['sha256']:raise ValueError('Held package bytes changed')
                packages.append((files,folder,manifest))
            old=packages[0][0].read(packages[0][1]/self.plan['payload']);new=packages[1][0].read(packages[1][1]/self.plan['payload'])
            other={row['path']:row['sha256'] for row in packages[0][2] if row['path'] in policy.preflight.PAYLOADS and row['path']!=self.plan['payload']}
            # Repeat signature authority after retaining package payload handles.
            self.authority(self.plan)
            self.store=ProductionMigrationStore(self.plan,self.files,self.locks,old,new,other,self.authority,initialize=initialize,mode=mode,fault=self.fault,initial_guard=(lambda existing:initial_guard(self,existing)) if initial_guard else None,resume_initialization=resume_initialization)
            self.key=self.store.key
            if initialize or resume_initialization:
                with self.store.operation():
                    self.store.initialization_only();initial_guard(self,True)
                    self.preservation=IndependentPreservation(self.store,self.native,initialize=True)
            else:self.preservation=IndependentPreservation(self.store,self.native,initialize=False)
        except BaseException:
            self.close()
            raise

    def session_context(self):
        snap=sessions_snapshot();owned=[row for row in snap['rows'] if row['sid']==self.context['sid']]
        current=q.owner_sid(q.W.HANDLE(-1))
        complete=snap['complete'] and current==self.context['sid'] and all(row['state']=='active' for row in owned)
        # Worker owner/session is checked freshly in authority; WTS policy checks
        # eligible logon sessions on every engine acquisition/check.
        return ProductionSession(current,snap['current_session'],self.binding['sid'],self.binding['session_id'],tuple(sorted(row['session_id'] for row in owned)),(),bool(complete))

    def authority(self,plan):
        if plan!=self.plan:raise ValueError('Caller plan changed')
        envelope=self.native.call();o=envelope['observation'];c=o['context'];t=o['task'];r=o['receipt']
        if c!=self.context or envelope['host']!=plan['host']:raise ValueError('Physical owner/profile/host changed')
        if o['packages']!={'r5':core.R5,'r7':core.R7,'entries':7,'unchanged':6}:raise ValueError('Signed closure changed')
        if envelope['normalized_task_sha256']!=self.binding['normalized_task_sha256'] or t['name']!=policy.TASK_NAME or t['path']!='\\' or t['enrollment_id']!=self.binding['enrollment_id'] or not t['owned_shape']:raise ValueError('Exact task definition changed')
        if r['sha256']!=plan['enrollment_sha256'] or r['id']!=self.binding['enrollment_id'] or r['package']!=core.R5 or r['phase']!='enrolled-disabled' or r['authenticated'] is not True:raise ValueError('Original R5 enrollment changed')
        if o['auth']!={'sha256':plan['auth_sha256'],'sidecar_matches':True,'host_matches':True}:raise ValueError('Original authorization changed')
        if o['pause_markers'] or o['competing_lifecycle_tasks']:raise ValueError('Pause/concurrent lifecycle work')
        for worker in o['workers']:
            if worker['sid']!=c['sid'] or worker['session_id']!=c['session_id'] or worker['kind'] not in ('wrapper','supervisor') or not worker['command_valid']:raise ValueError('Unsupported worker/context')
        entries={row['name']:row for row in o['runtime']}
        if len(o['runtime'])!=5 or set(entries)!=policy.preflight.PAYLOADS:raise ValueError('Runtime closure')
        for name,row in entries.items():
            if name!=plan['payload'] and (row['sha256']!=row['r5_sha256'] or row['r5_sha256']!=row['r7_sha256']):raise ValueError('Unchanged signed runtime drift')
        self.latest=envelope
        return True

    def outer_lock(self):return self.store.operation()
    def phase(self):
        raw=self.store.read_journal()
        return self.store.phase(raw) if raw is not None else None
    def read_journal(self):return self.store.read_journal()
    def write_journal(self,raw):return self.store.write_journal(raw)
    def verify_original_enrollment(self,plan):self.authority(plan)

    def verify(self,plan):
        if self.mode!='forward':raise ValueError('Forward verification unavailable during rollback')
        phase=self.phase();task=self.latest['observation']['task']
        allowed=({plan['original_enabled']} if phase in (None,'prepared','start','complete') else ({False,plan['original_enabled']} if phase in ('disable','restore') else {False}))
        if task['enabled'] not in allowed:raise ValueError('Enabled state not authorized by authenticated phase')
        if phase in (None,'prepared','disable','stop') and plan['original_running'] and self.latest['observation']['workers'] and self.original_pair is None:
            self.original_pair=CapturedPair(plan,file_probe=self.images.probe);self.pairs.append(self.original_pair)

    def assert_original(self,plan):
        self.verify(plan);task=self.latest['observation']['task']
        expected='Running' if plan['original_running'] else ('Ready' if plan['original_enabled'] else 'Disabled')
        if task['enabled']!=plan['original_enabled'] or task['state']!=expected:raise ValueError('Original task baseline changed')
        if not plan['original_running'] and self.latest['observation']['workers']:raise ValueError('Stopped baseline has workers')
        if plan['original_running'] and self.original_pair is None:raise ValueError('Missing retained original pair')

    def disable_owned(self,plan):
        self.verify(plan);self.assert_preservation()
        self.native.call('Disable',self.binding,self.latest['observation']['task']['enabled'],plan['original_enabled'])

    def stop_captured_pair(self,plan):
        self.verify(plan)
        if self.original_pair:self.original_pair.stop_wrapper()
        elif plan['original_running']:
            # Lost stop completion: independently prove both original identities
            # gone. A reused PID is not the original and is never terminated.
            for field in ('wrapper_identity','child_identity'):
                row=json.loads(plan[field]);handle=q.Open(0x100000|0x1000,False,row['pid'])
                if not handle:
                    if ctypes.get_last_error()==87:continue
                    q.require(False)
                try:
                    status=q.Wait(handle,0)
                    if status not in (0,258):raise ValueError('Original process wait unknown')
                    if status==0:continue
                    if int(q.identity(handle)['created'])+DOTNET_FILETIME_DELTA==row['creation_ticks']:raise ValueError('Original process still alive without retained pair')
                finally:q.require(q.Close(handle))
        self.authority(plan)
        if self.latest['observation']['workers'] or self.latest['observation']['task']['state']=='Running':raise ValueError('Task/process stop has not converged')
        self.assert_preservation()

    @contextmanager
    def quiescent(self,plan):
        self.authority(plan)
        if self.latest['observation']['workers'] or self.latest['observation']['task']['enabled']:raise ValueError('Disabled quiescent task required')
        with self.locks.quiescent():
            self.authority(plan)
            if self.latest['observation']['workers']:raise ValueError('Workers appeared under runtime locks')
            yield

    def backup_verified(self,plan):return self.store.backup_verified(plan)
    def replace_owned(self,plan):return self.store.replace_owned(plan)
    def restore_enabled(self,plan):
        self.verify(plan);self.locks.assert_restart_allowed()
        self.native.call('Restore',self.binding,self.latest['observation']['task']['enabled'],plan['original_enabled'])

    def current_pair(self):
        captured=capture_observation_pair(self.latest,self.images)
        plan=dict(self.plan,original_running=True,original_enabled=True,wrapper_identity=policy.canonical_identity(captured['wrapper']),child_identity=policy.canonical_identity(captured['supervisor']))
        owner=CapturedPair(plan,file_probe=self.images.probe);self.pairs.append(owner)
        return captured

    def start_once(self,plan):
        self.verify(plan);self.locks.assert_restart_allowed()
        intent_path=self.store.directory/'start-intent.json';receipt_path=self.store.directory/'start-receipt.json'
        fresh=not intent_path.exists()
        if fresh and receipt_path.exists():raise ValueError('Orphan start receipt preserved; missing intent')
        if fresh:
            if self.latest['observation']['workers'] or self.latest['observation']['task']['state']!='Ready':raise ValueError('Start intent requires proven stopped ready task')
            value=dict(nonce=secrets.token_hex(16),not_before_ticks=time.time_ns()//100+621355968000000000)
            self.files.create(intent_path,self.store.seal_record('start-intent',value))
        intent=self.start_intent()
        if receipt_path.exists():self.assert_current_start(intent);return
        if not self.latest['observation']['workers']:
            if not fresh:raise ValueError('Pending Start absent pair is ambiguous; no automatic retry')
            self.locks.assert_restart_allowed();self.fault('before-start-call')
            self.native.call('Start',self.binding,True,plan['original_enabled']);self.fault('after-start-call')
        deadline=time.monotonic()+30
        while True:
            self.authority(plan)
            if self.latest['observation']['task']['state']=='Running' and len(self.latest['observation']['workers'])==2:break
            if time.monotonic()>=deadline:raise TimeoutError('Start convergence unknown')
            time.sleep(.2)
        pair=self.current_pair()
        if any(row['creation_ticks']<intent['not_before_ticks'] for row in pair.values()):raise ValueError('Current pair predates owned Start intent')
        self.files.create(receipt_path,self.store.seal_record('start-receipt',dict(intent=intent,pair=pair)))
        self.fault('after-start-receipt')

    def start_intent(self):
        value=self.store.record('start-intent.json','start-intent')
        return self.validate_start_intent(value)

    @staticmethod
    def validate_start_intent(value):
        if type(value) is not dict or set(value)!={'nonce','not_before_ticks'} or type(value['not_before_ticks']) is not int or value['not_before_ticks']<=0 or not isinstance(value['nonce'],str) or not policy.re.fullmatch('[a-f0-9]{32}',value['nonce']):raise ValueError('Start intent schema')
        return value

    def assert_current_start(self,intent):
        if self.latest['observation']['task']['state']!='Running':raise ValueError('Current started task is not Running')
        value=self.store.record('start-receipt.json','start-receipt')
        pair=self.current_pair()
        if value!={'intent':intent,'pair':pair} or any(row['creation_ticks']<intent['not_before_ticks'] for row in pair.values()):raise ValueError('Current pair/start receipt mismatch')

    def assert_complete(self,plan):
        self.verify(plan);self.store.verify_replacement();self.assert_preservation()
        if self.latest['observation']['task']['enabled']!=plan['original_enabled']:raise ValueError('Original enabled state not restored')
        if plan['original_running']:self.assert_current_start(self.start_intent())
        elif self.latest['observation']['task']['state']!=('Ready' if plan['original_enabled'] else 'Disabled') or self.latest['observation']['workers'] or (self.store.directory/'start-intent.json').exists() or (self.store.directory/'start-receipt.json').exists():raise ValueError('Stopped baseline must remain stopped')

    def run(self):
        if self.mode!='forward':raise ValueError('Explicit rollback operation required')
        return core.migrate(self,self.plan,self.key)

    def rollback(self):
        """Separate rollback lifecycle; forward journal is never rewritten.

        Refuses ambiguous restart completion just like forward Start. Filesystem
        receipt alone cannot establish task restoration or rollback completion.
        """
        if self.mode!='rollback':raise ValueError('Explicit rollback provider required')
        with self.outer_lock():
            self.store.rollback_state();self.authority(self.plan);self.assert_preservation()
            path=self.store.directory/'rollback-task-intent.json'
            if not path.exists():
                task=self.latest['observation']['task'];workers=self.latest['observation']['workers'];pair=None
                if task['state'] not in ('Ready','Disabled','Running') or (task['state']=='Running')!=bool(workers):raise ValueError('Rollback task state not converged')
                if workers:
                    if not self.plan['original_running']:raise ValueError('Originally stopped task has unexpected pair')
                    intent=self.start_intent();pair=self.current_pair()
                    if any(row['creation_ticks']<intent['not_before_ticks'] for row in pair.values()):raise ValueError('Rollback cannot own a pre-intent pair')
                    if (self.store.directory/'start-receipt.json').exists():self.assert_current_start(intent)
                value=dict(pair=pair,forward_journal=hashlib.sha256(self.files.read(self.store.journal,65536)).hexdigest())
                self.files.create(path,self.store.seal_record('rollback-task-intent',value))
            task_intent=self.store.record(path.name,'rollback-task-intent')
            if type(task_intent) is not dict or set(task_intent)!={'pair','forward_journal'} or task_intent['forward_journal']!=hashlib.sha256(self.files.read(self.store.journal,65536)).hexdigest():raise ValueError('Rollback task intent binding')
            if task_intent['pair'] is not None:
                pair=task_intent['pair']
                if type(pair) is not dict or set(pair)!={'wrapper','supervisor'} or not self.plan['original_running']:raise ValueError('Rollback captured pair schema')
                policy.validate_production_plan(dict(self.plan,original_running=True,original_enabled=True,wrapper_identity=policy.canonical_identity(pair['wrapper']),child_identity=policy.canonical_identity(pair['supervisor'])))
            phases=('disable','stop','files','restore','start','complete')
            journal=self.store.directory/'rollback-task-phase.json'
            if journal.exists():
                state=self.store.record(journal.name,'rollback-task-phase')
                if type(state) is not dict or set(state)!={'phase','intent'} or state['phase'] not in phases or state['intent']!=task_intent:raise ValueError('Rollback phase binding')
                initial=phases.index(state['phase'])
            else:initial=0
            enabled=self.latest['observation']['task']['enabled']
            if (initial in (1,2) and enabled) or (initial in (4,5) and enabled!=self.plan['original_enabled']):raise ValueError('Rollback enabled state not authorized by phase')
            def checkpoint(phase):
                data=self.store.seal_record('rollback-task-phase',dict(phase=phase,intent=task_intent))
                pending=self.store.directory/'rollback-task-phase.pending'
                if pending.exists():
                    if self.files.read(pending)!=data:raise ValueError('Ambiguous rollback pending phase')
                else:self.files.create(pending,data)
                if journal.exists():self.files.replace(pending,journal,self.files.read(journal))
                else:self.files.publish_new(pending,journal,data)
                if self.files.read(journal)!=data:raise ValueError('Rollback phase readback')
            if initial<=0:
                checkpoint('disable');self.authority(self.plan)
                self.native.call('Disable',self.binding,self.latest['observation']['task']['enabled'],self.plan['original_enabled'])
            if initial<=1:
                checkpoint('stop');self.authority(self.plan)
                if self.latest['observation']['workers']:
                    pair=task_intent['pair']
                    if pair is None:raise ValueError('Unexpected rollback pair')
                    plan=dict(self.plan,original_enabled=True,original_running=True,wrapper_identity=policy.canonical_identity(pair['wrapper']),child_identity=policy.canonical_identity(pair['supervisor']))
                    owned=CapturedPair(plan,file_probe=self.images.probe);self.pairs.append(owned);owned.stop_wrapper()
                elif task_intent['pair'] is not None:
                    for row in task_intent['pair'].values():
                        handle=q.Open(0x100000|0x1000,False,row['pid'])
                        if not handle:
                            if ctypes.get_last_error()==87:continue
                            q.require(False)
                        try:
                            status=q.Wait(handle,0)
                            if status not in (0,258):raise ValueError('Rollback original process wait unknown')
                            if status==258 and int(q.identity(handle)['created'])+DOTNET_FILETIME_DELTA==row['creation_ticks']:raise ValueError('Rollback captured process still alive')
                        finally:q.require(q.Close(handle))
                self.authority(self.plan)
                if self.latest['observation']['workers'] or self.latest['observation']['task']['state']=='Running':raise ValueError('Rollback stop not converged')
                self.assert_preservation()
            if initial<=2:
                checkpoint('files')
                with self.quiescent(self.plan):self.store.rollback_owned(self.plan)
            if initial<=3:
                checkpoint('restore');self.store.rollback_state();self.authority(self.plan);self.locks.assert_restart_allowed()
                self.native.call('Restore',self.binding,self.latest['observation']['task']['enabled'],self.plan['original_enabled'])
            if initial<=4:
                checkpoint('start')
                if self.plan['original_running']:self.rollback_start()
            self.assert_rollback_complete();checkpoint('complete')
            return dict(status='rolled-back',forward_journal_preserved=True)

    def rollback_start(self):
        self.store.rollback_state();self.authority(self.plan);self.locks.assert_restart_allowed()
        intent_path=self.store.directory/'rollback-start-intent.json';receipt=self.store.directory/'rollback-start-receipt.json'
        fresh=not intent_path.exists()
        if fresh and receipt.exists():raise ValueError('Orphan rollback start receipt preserved')
        if fresh:
            if self.latest['observation']['workers'] or self.latest['observation']['task']['state']!='Ready':raise ValueError('Rollback restart requires stopped ready task')
            self.files.create(intent_path,self.store.seal_record('rollback-start-intent',dict(nonce=secrets.token_hex(16),not_before_ticks=time.time_ns()//100+621355968000000000)))
        intent=self.validate_start_intent(self.store.record(intent_path.name,'rollback-start-intent'))
        if not receipt.exists():
            if not self.latest['observation']['workers']:
                if not fresh:raise ValueError('Ambiguous rollback Start; no retry')
                self.locks.assert_restart_allowed();self.native.call('Start',self.binding,True,True)
            deadline=time.monotonic()+30
            while True:
                self.authority(self.plan)
                if self.latest['observation']['task']['state']=='Running' and len(self.latest['observation']['workers'])==2:break
                if time.monotonic()>=deadline:raise TimeoutError('Rollback restart convergence')
                time.sleep(.2)
            pair=self.current_pair()
            if any(row['creation_ticks']<intent['not_before_ticks'] for row in pair.values()):raise ValueError('Rollback pair predates restart')
            self.files.create(receipt,self.store.seal_record('rollback-start-receipt',dict(intent=intent,pair=pair)))
        if self.latest['observation']['task']['state']!='Running' or self.store.record(receipt.name,'rollback-start-receipt')!={'intent':intent,'pair':self.current_pair()}:raise ValueError('Rollback restart receipt/current mismatch')

    def assert_rollback_complete(self):
        self.store.rollback_state();self.authority(self.plan);self.assert_preservation()
        if not (self.store.directory/'rollback-file-receipt.json').exists() or self.files.read(self.store.target)!=self.store.old:raise ValueError('Rollback file restoration incomplete')
        task=self.latest['observation']['task']
        if task['enabled']!=self.plan['original_enabled']:raise ValueError('Rollback enabled restoration incomplete')
        if self.plan['original_running']:
            receipt=self.store.record('rollback-start-receipt.json','rollback-start-receipt');intent=self.validate_start_intent(self.store.record('rollback-start-intent.json','rollback-start-intent'))
            pair=self.current_pair()
            if task['state']!='Running' or receipt!={'intent':intent,'pair':pair} or any(row['creation_ticks']<intent['not_before_ticks'] for row in pair.values()):raise ValueError('Rollback running restoration incomplete')
        elif task['state']!=('Ready' if self.plan['original_enabled'] else 'Disabled') or self.latest['observation']['workers'] or (self.store.directory/'rollback-start-intent.json').exists() or (self.store.directory/'rollback-start-receipt.json').exists():raise ValueError('Rollback stopped restoration incomplete')

    def assert_preservation(self):
        if self.preservation is None:raise ValueError('Original independent process baseline unavailable')
        return self.preservation.assert_alive()

    def close(self):
        if self.closed:return
        errors=[]
        for owner in self.pairs:
            try:owner.close()
            except BaseException as error:errors.append(repr(error))
        cleanups=[self.images.close,self.package_handles.close,self.native.close]
        if self.preservation is not None:cleanups.insert(0,self.preservation.close)
        for cleanup in cleanups:
            try:cleanup()
            except BaseException as error:errors.append(repr(error))
        self.closed=True
        if errors:raise RuntimeError('Provider retained handle cleanup uncertainty: '+repr(errors))
