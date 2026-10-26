"""Read-only concrete owner-profile plan capture. Prints private JSON only.

No provider/store construction, key creation, task operation, file write or stop.
Query-only original process and read-only image/module handles remain retained
through final observation validation. The result expires and grants no activation.
"""
import json,secrets
import active_host_migration_core as core
import active_host_migration_windows as policy
from active_host_migration_windows_native import NativeOperations,capture_observation_pair
from active_host_migration_windows_images import ImagePins
from active_host_migration_windows_processes import q,DOTNET_FILETIME_DELTA,command_snapshot
from inspect_active_host_migration import sessions_snapshot

MODULE_SHA256='92f12b8d60ece5116b5f53481b091bfdeca908b57d6ec8f24aa8c2d1e29b187e'

def prepare():
    native=NativeOperations(MODULE_SHA256);images=ImagePins();handles=[];result=None;errors=[]
    try:
        envelope=native.call();sessions=sessions_snapshot()
        initial=policy.preflight.evaluate(envelope['observation'],sessions)
        if initial['status']!='preflight_observations_valid':raise ValueError('Fresh initial R5 preflight refused: '+repr(initial['reasons']))
        pair=capture_observation_pair(envelope,images,handles) if envelope['observation']['task']['state']=='Running' else {}
        plan=policy.build_plan(envelope,sessions,pair,secrets.token_hex(16))
        final=native.call()
        # Collection timestamps vary; authority/task/runtime/worker bindings must not.
        before=dict(envelope['observation']);after=dict(final['observation'])
        before.pop('observed_utc');after.pop('observed_utc')
        if before!=after or envelope['host']!=final['host'] or envelope['task_xml']!=final['task_xml'] or envelope['normalized_task_sha256']!=final['normalized_task_sha256']:raise ValueError('Owner observation changed during held capture')
        if sessions_snapshot()!=sessions:raise ValueError('Session changed during capture')
        for handle,row in handles:
            actual=q.identity(handle)
            if q.Wait(handle,0)!=258 or int(actual['created'])+DOTNET_FILETIME_DELTA!=row['creation_ticks'] or actual['owner']!=row['sid'] or policy.preflight.path(actual['image'])!=policy.preflight.path(row['image']) or command_snapshot(row['pid'])!={name:row[name] for name in ('pid','parent_pid','session_id','command_line')}:raise ValueError('Original retained process changed')
        result=dict(schema=1,inspection_only=True,production_activation_available=False,plan=plan,observation=final,sessions=sessions,held_query_processes=len(handles),module_sha256=MODULE_SHA256)
    finally:
        for handle,_ in handles:
            try:
                if not q.Close(handle):raise RuntimeError('Query process handle close failed')
            except BaseException as error:errors.append(repr(error))
        for close in (images.close,native.close):
            try:close()
            except BaseException as error:errors.append(repr(error))
        if errors:raise RuntimeError('Read-only capture cleanup uncertainty: '+repr(errors))
    return result

if __name__=='__main__':print(json.dumps(prepare(),sort_keys=True))
