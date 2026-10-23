"""Fixed production native bridge; no CLI or automatic operation on import."""
import base64,hashlib,json,subprocess
from pathlib import Path
import active_host_migration_core as core
from active_host_migration_windows_processes import q,command_snapshot,DOTNET_FILETIME_DELTA
from active_host_migration_windows_images import ImagePins
import active_host_migration_windows as policy

MODULE=Path(__file__).resolve().parent/'ActiveHostMigration.Native.psm1'

class NativeOperations:
    def __init__(self,reviewed_module_sha256):
        policy.preflight.hash_value(reviewed_module_sha256)
        self.pin=reviewed_module_sha256
        self.module_handles=ImagePins()
        try:
            if self.module_handles.probe(str(MODULE))['sha256']!=self.pin:raise ValueError('Reviewed native module changed')
        except BaseException:
            self.module_handles.close()
            raise

    def close(self):self.module_handles.close()

    def call(self,operation='Observe',binding=None,expected_enabled=False,original_enabled=False):
        if operation not in ('Observe','Independents','Disable','Restore','Start'):raise ValueError('Fixed native operation required')
        if self.module_handles.probe(str(MODULE))['sha256']!=self.pin:raise ValueError('Reviewed native module changed')
        path=str(MODULE).replace("'","''")
        code="$ErrorActionPreference='Stop';$m=Import-Module '"+path+"' -Force -PassThru;"
        if operation=='Observe':code+='$value=Get-ActiveMigrationObservation;'
        elif operation=='Independents':code+='$value=Get-MigrationIndependentProcesses;'
        else:
            if type(expected_enabled) is not bool or type(original_enabled) is not bool:raise ValueError('Exact enabled booleans')
            payload=base64.b64encode(core.encode(binding)).decode()
            code+="$b=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+payload+"'))|ConvertFrom-Json;"
            code+="$value=& $m {param($b) Invoke-MigrationTaskOperation -Operation '"+operation+"' -Binding $b -ExpectedEnabled $"+str(expected_enabled).lower()+" -OriginalEnabled $"+str(original_enabled).lower()+"} $b;"
        code+='$value|ConvertTo-Json -Depth 14 -Compress'
        result=subprocess.run([str(q.PS7),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or len(result.stdout.encode())>1048576:raise ValueError('Native operation refused: '+result.stderr[:1000])
        return json.loads(result.stdout)


def capture_observation_pair(envelope,image_pins,retained_query_handles=None):
    candidates=[row for row in envelope['observation']['workers'] if row['kind'] in ('wrapper','supervisor')]
    rows={row['kind']:row for row in candidates}
    if len(candidates)!=2 or set(rows)!={'wrapper','supervisor'}:raise ValueError('Exactly one current pair required')
    result={}
    for role,row in rows.items():
        handle=q.Open(0x100000|0x1000,False,row['pid']);q.require(handle)
        try:
            native=q.identity(handle);command=command_snapshot(row['pid']);physical=image_pins.probe(row['image'])
            value=dict(pid=row['pid'],parent_pid=command['parent_pid'],creation_ticks=int(native['created'])+DOTNET_FILETIME_DELTA,sid=native['owner'],session_id=command['session_id'],image=native['image'],image_sha256=physical['sha256'],command_line=command['command_line'],file_identity=physical['file_identity'])
            policy.process_identity(row,value)
            if q.Wait(handle,0)!=258 or q.identity(handle)!=native:raise ValueError('Pair changed during capture')
            result[role]=value
            if retained_query_handles is not None:
                retained_query_handles.append((handle,value));handle=None
        finally:
            if handle:q.require(q.Close(handle))
    policy.validate_pair_commands(envelope['observation']['context']['root'],result['wrapper'],result['supervisor'])
    return result
