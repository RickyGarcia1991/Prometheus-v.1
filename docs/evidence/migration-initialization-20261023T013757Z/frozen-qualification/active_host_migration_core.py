"""Development-only active R5->R7 HostAgent migration state machine.
No native provider, CLI, live task/process/file operation or installation entry.
"""
import hashlib,hmac,json,re

R5='b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e'
R7='6e26224389829b4f9cfb3288b8f9495d0e285e9fc96a322f3db25e82ecddc01e'
PHASES=('prepared','disable','stop','backup','replace','restore','start','complete')

def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def validate(plan):
 required={'transaction','host','profile','auth_sha256','enrollment_sha256','task_config_sha256','task_identity','original_enabled','original_running','old_manifest','new_manifest','old_file_sha256','new_file_sha256','payload','wrapper_identity','child_identity'}
 if set(plan)!=required:raise ValueError('Exact plan schema required')
 if not re.fullmatch('[a-f0-9]{32}',plan['transaction']):raise ValueError('Transaction')
 for name in ('host','auth_sha256','enrollment_sha256','task_config_sha256','old_file_sha256','new_file_sha256'):
  if not isinstance(plan[name],str) or not re.fullmatch('[a-f0-9]{64}',plan[name]):raise ValueError(name)
 if plan['old_manifest']!=R5 or plan['new_manifest']!=R7:raise ValueError('Pinned package mismatch')
 if plan['payload']!='Prometheus-Host-Agent.ps1' or plan['old_file_sha256']==plan['new_file_sha256']:raise ValueError('Only changed HostAgent allowed')
 for field in ('profile','task_identity','wrapper_identity','child_identity'):
  if not isinstance(plan[field],str) or not 0<len(plan[field])<=4096:raise ValueError(field)
 if type(plan['original_enabled']) is not bool or type(plan['original_running']) is not bool:raise ValueError('Task flags')
 if plan['original_running'] and not plan['original_enabled']:raise ValueError('Unsupported disabled-running baseline')


def seal(plan,phase,key):
 body=encode({'plan':plan,'phase':phase})
 return encode({'body':body.decode(),'tag':hmac.new(key,b'active-host-migration-v1\0'+body,hashlib.sha256).hexdigest()})


def migrate(provider,plan,key):
 """Provider promises exact owned operations, CAS and durable sealed journal.

 outer_lock: enrollment then eject A..Z (nonblocking, reverse release).
 quiescent: acquired after captured wrapper/child stop; UsbLifecycle then Arrival then HostAgent, all nonblocking; no
 other-session owned workers allowed; release runtime locks BEFORE task restart.
 verify validates both exact signed closures, unchanged other six entries, physical
 root/current SID and unchanged authorization, task config/identity (allowing only
 transaction-owned temporary enabled flip). No task recreation or broad stop.
 start_once must prove owned current task/process identity and suppress duplicates.
 Recovery replays a pending phase idempotently; ambiguity fails without deletion.
 """
 validate(plan)
 if not isinstance(key,bytes) or len(key)<32:raise ValueError('Protected key required')
 with provider.outer_lock():
  provider.verify(plan)
  provider.verify_original_enrollment(plan)
  raw=provider.read_journal()
  if raw is None:
   provider.assert_original(plan)
   phase='prepared';provider.write_journal(seal(plan,phase,key))
  else:
   if not isinstance(raw,bytes) or len(raw)>65536:raise ValueError('Journal bound')
   record=json.loads(raw);body=record['body'].encode()
   if not hmac.compare_digest(record['tag'],hmac.new(key,b'active-host-migration-v1\0'+body,hashlib.sha256).hexdigest()):raise ValueError('Journal authentication')
   decoded=json.loads(body)
   phase=decoded['phase']
   if decoded['plan']!=plan or phase not in PHASES or raw!=seal(plan,phase,key):raise ValueError('Journal binding')
  def checkpoint(next_phase):
   provider.write_journal(seal(plan,next_phase,key))
   if provider.read_journal()!=seal(plan,next_phase,key):raise ValueError('Journal readback')
  start=PHASES.index(phase)
  if phase=='complete':
   provider.assert_complete(plan)
   return {'status':'complete','task_recreated':False,'live_adapter':False}
  # Persist each intent BEFORE performing its operation. A lost completion replays
  # the same owned operation, never infers ownership from matching bytes alone.
  for step,operation in [('disable',provider.disable_owned),('stop',provider.stop_captured_pair)]:
   if start<=PHASES.index(step):checkpoint(step);operation(plan)
  if start<=PHASES.index('replace'):
   with provider.quiescent(plan):
    provider.verify(plan)
    if start<=PHASES.index('backup'):checkpoint('backup');provider.backup_verified(plan)
    checkpoint('replace');provider.replace_owned(plan)
  if start<=PHASES.index('restore'):checkpoint('restore');provider.restore_enabled(plan)
  if start<=PHASES.index('start'):
   checkpoint('start')
   if plan['original_running']:provider.start_once(plan)
  provider.assert_complete(plan)
  checkpoint('complete')
  return {'status':'complete','task_recreated':False,'live_adapter':False}


def verify_completed_chain(provider,plan,key):
 """Read-only new-consumer bridge, never rewrites/reinterprets R5 enrollment.

 verify_original_enrollment must authenticate the immutable R5 enrollment receipt,
 compare its exact bytes to enrollment_sha256 and bind original task/auth/package.
 verify must validate both signed closures and prove only the HostAgent entry changed.
 Old R5 consumers still reject changed runtime bytes; they do not call this bridge.
 """
 validate(plan)
 if not isinstance(key,bytes) or len(key)<32:raise ValueError('Protected key required')
 with provider.outer_lock():
  provider.verify_original_enrollment(plan)
  provider.verify(plan)
  raw=provider.read_journal()
  if not isinstance(raw,bytes) or not hmac.compare_digest(raw,seal(plan,'complete',key)):
   raise ValueError('Completed authenticated migration chain required')
  provider.assert_complete(plan)
  return {'enrollment_sha256':plan['enrollment_sha256'],'old_manifest':R5,'new_manifest':R7,'task_identity':plan['task_identity'],'migration_transaction':plan['transaction']}

