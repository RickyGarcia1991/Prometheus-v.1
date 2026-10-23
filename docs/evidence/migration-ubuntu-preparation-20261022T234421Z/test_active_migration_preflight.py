import copy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import active_migration_preflight as p
import inspect_active_host_migration as entry


@pytest.fixture
def observations():
    sid='S-1-5-21-1-2-3-1002';root=r'C:\Users\fixture\AppData\Local\Prometheus'
    runtime=[dict(name=name,sha256=str(i)*64,r5_sha256=str(i)*64,r7_sha256=('f'*64 if name=='Prometheus-Host-Agent.ps1' else str(i)*64)) for i,name in enumerate(sorted(p.PAYLOADS),1)]
    wrapper=next(row['sha256'] for row in runtime if row['name']=='Prometheus-QuietHost-v3.exe')
    o=dict(schema=1,collected=True,observed_utc='2026-10-22T00:00:00Z',context=dict(sid=sid,session_id=1,profile_root=r'C:\Users\fixture',physical_local=r'C:\Users\fixture\AppData\Local',reported_local=r'C:\Users\fixture\AppData\Local',root=root,classification='physical-profile'),packages=dict(r5=p.R5,r7=p.R7,entries=7,unchanged=6),receipt=dict(id='a'*32,sha256='b'*64,phase='enrolled-disabled',package=p.R5,authenticated=True),auth=dict(sha256='c'*64,sidecar_matches=True,host_matches=True),task=dict(name='Prometheus USB Reconnect Supervisor',path='\\',enrollment_id='a'*32,config_sha256='d'*64,enabled=True,state='Running',owned_shape=True),runtime=runtime,workers=[dict(kind='wrapper',pid=100,parent_pid=50,creation_ticks=1000,sid=sid,session_id=1,image=root+r'\Prometheus-QuietHost-v3.exe',image_sha256=wrapper,command_valid=True),dict(kind='supervisor',pid=101,parent_pid=100,creation_ticks=1001,sid=sid,session_id=1,image=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',image_sha256='e'*64,command_valid=True)],pause_markers=[],migration_evidence=[],competing_lifecycle_tasks=[])
    sessions=dict(complete=True,current_session=1,rows=[dict(session_id=1,sid=sid,state='active')])
    return o,sessions


def test_valid_snapshot_is_never_mutation_authority(observations):
    o,s=observations;before=copy.deepcopy((o,s))
    r=p.evaluate(o,s)
    assert r['status']=='preflight_observations_valid'
    assert r['current_runtime_phase']=='original_r5'
    assert not r['mutation_ready'] and not r['can_execute'] and not r['cross_session_exclusion_proven']
    assert r['stale_observation_caveat']
    assert (o,s)==before


@pytest.mark.parametrize('enabled,state',[(False,'Disabled'),(True,'Ready')])
def test_stopped_baseline_supported(observations,enabled,state):
    o,s=observations;o['task'].update(enabled=enabled,state=state);o['workers']=[]
    assert p.evaluate(o,s)['status']=='preflight_observations_valid'


@pytest.mark.parametrize('case',['redirected-profile','wrong-physical-path','wrong-root','incomplete-sessions','other-session','disconnected-session','wrong-current-session','wrong-package','wrong-receipt','wrong-receipt-phase','wrong-auth','wrong-host','wrong-task-id','wrong-task-shape','disabled-running','scheduler-transition','runtime-tamper','r7-runtime','other-entry-delta','duplicate-wrapper','missing-child','wrong-parent','child-too-old','other-worker-session','other-worker-owner','wrapper-image','wrapper-hash','wrapper-command','child-command','arrival-active','pause','competing-task'])
def test_refuses_unsafe_or_incomplete_observations(observations,case):
    o,s=observations
    if case=='redirected-profile':o['context']['classification']='msix-redirected-profile'
    elif case=='wrong-physical-path':o['context']['reported_local']=r'C:\Users\fixture\AppData\Local\Packages\Codex\LocalCache\Local'
    elif case=='wrong-root':o['context']['root']=r'C:\different'
    elif case=='incomplete-sessions':s['complete']=False
    elif case=='other-session':s['rows'].append(dict(session_id=2,sid=o['context']['sid'],state='active'))
    elif case=='disconnected-session':s['rows'][0]['state']='disconnected'
    elif case=='wrong-current-session':s['current_session']=2
    elif case=='wrong-package':o['packages']['r7']=p.R5
    elif case=='wrong-receipt':o['receipt']['authenticated']=False
    elif case=='wrong-receipt-phase':o['receipt']['phase']='copying'
    elif case=='wrong-auth':o['auth']['sidecar_matches']=False
    elif case=='wrong-host':o['auth']['host_matches']=False
    elif case=='wrong-task-id':o['task']['enrollment_id']='f'*32
    elif case=='wrong-task-shape':o['task']['owned_shape']=False
    elif case=='disabled-running':o['task']['enabled']=False
    elif case=='scheduler-transition':o['task']['state']='Queued'
    elif case=='runtime-tamper':o['runtime'][0]['sha256']='0'*64
    elif case=='r7-runtime':
        for row in o['runtime']:row['sha256']=row['r7_sha256']
    elif case=='other-entry-delta':o['runtime'][0]['r7_sha256']='0'*64
    elif case=='duplicate-wrapper':
        row=copy.deepcopy(o['workers'][0]);row['pid']=102;o['workers'].append(row)
    elif case=='missing-child':o['workers'].pop()
    elif case=='wrong-parent':o['workers'][1]['parent_pid']=999
    elif case=='child-too-old':o['workers'][1]['creation_ticks']=999
    elif case=='other-worker-session':o['workers'][0]['session_id']=2
    elif case=='other-worker-owner':o['workers'][0]['sid']='S-1-5-21-4-5-6-1002'
    elif case=='wrapper-image':o['workers'][0]['image']=r'C:\foreign.exe'
    elif case=='wrapper-hash':o['workers'][0]['image_sha256']='0'*64
    elif case=='wrapper-command':o['workers'][0]['command_valid']=False
    elif case=='child-command':o['workers'][1]['command_valid']=False
    elif case=='arrival-active':
        row=copy.deepcopy(o['workers'][1]);row.update(kind='arrival',pid=102);o['workers'].append(row)
    elif case=='pause':o['pause_markers']=['eject-mode.json']
    elif case=='competing-task':o['competing_lifecycle_tasks']=['Prometheus Volume Watcher']
    assert p.evaluate(o,s)['status']=='refused'


@pytest.mark.parametrize('damage',['extra-field','missing-field','bool-as-int','nonbool','duplicate-runtime','duplicate-session','bad-hash','unknown-worker'])
def test_strict_schema_rejects_untrusted_observations(observations,damage):
    o,s=observations
    if damage=='extra-field':o['mutation_permission']=True
    elif damage=='missing-field':del o['auth']
    elif damage=='bool-as-int':o['workers'][0]['pid']=True
    elif damage=='nonbool':o['task']['enabled']=1
    elif damage=='duplicate-runtime':o['runtime'][0]=copy.deepcopy(o['runtime'][1])
    elif damage=='duplicate-session':s['rows'].append(copy.deepcopy(s['rows'][0]))
    elif damage=='bad-hash':o['receipt']['sha256']='unknown'
    elif damage=='unknown-worker':o['workers'][0]['kind']='arbitrary'
    assert p.evaluate(o,s)['status']=='incomplete_context'


def test_pending_evidence_names_never_assert_completed_phase(observations):
    o,s=observations;o['migration_evidence']=['complete.json']
    r=p.evaluate(o,s)
    assert r['status']=='pending_recovery' and not r['mutation_ready']


def test_collector_failure_carries_no_installed_state_claim(observations):
    _,s=observations
    o=dict(schema=1,collected=False,stage='context',error='MSIX/file redirection refused',context=None)
    r=p.evaluate(o,s)
    assert r['status']=='incomplete_context' and r['current_runtime_phase']=='unobserved'


def test_entry_only_invokes_fixed_readonly_collector(observations,monkeypatch):
    o,s=observations;calls=[]
    def runner(args,**kwargs):
        calls.append((args,kwargs));return SimpleNamespace(returncode=0,stdout=json.dumps(o))
    def forbidden(*a,**kw):raise AssertionError('Unexpected filesystem mutation')
    monkeypatch.setattr(Path,'write_text',forbidden);monkeypatch.setattr(Path,'write_bytes',forbidden)
    report=entry.inspect(runner=runner,session_collector=lambda:s)
    assert report['status']=='preflight_observations_valid'
    assert calls==[([str(entry.PWSH),'-NoProfile','-NonInteractive','-File',str(entry.COLLECTOR)],dict(capture_output=True,text=True,timeout=45))]


def test_timeout_returns_incomplete_without_recovery_mutations(observations):
    _,s=observations;calls=[]
    def runner(args,**kwargs):calls.append(args);raise subprocess.TimeoutExpired(args,45)
    assert entry.inspect(runner=runner,session_collector=lambda:s)['status']=='incomplete_context'
    assert len(calls)==1


def test_native_collector_contains_no_mutation_commands():
    script=r'''
$ast=[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$null,[ref]$null)
$commands=@($ast.FindAll({param($node)$node -is [Management.Automation.Language.CommandAst]},$true)|ForEach-Object {$_.GetCommandName()}|Where-Object {$_})
$commands|ConvertTo-Json -Compress
'''
    script=script.replace('$args[0]', "'"+str(entry.COLLECTOR).replace("'","''")+"'")
    run=subprocess.run([str(entry.PWSH),'-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=10)
    assert run.returncode==0,run.stderr
    commands=json.loads(run.stdout)
    assert 'Get-ScheduledTask' in commands and 'Read-Locked' in commands
    forbidden={'Set-Content','Add-Content','Out-File','Remove-Item','Copy-Item','Move-Item','New-Item','Start-Process','Stop-Process','Start-ScheduledTask','Stop-ScheduledTask','Register-ScheduledTask','Unregister-ScheduledTask','Enable-ScheduledTask','Disable-ScheduledTask','Initialize-ReconnectAuthorization','Protect-EnrollmentJournal','Start-ReconnectEnrollment'}
    assert not forbidden.intersection(commands)
    source=entry.COLLECTOR.read_text()
    assert "[IO.File]::Open($Path,'Open','Read','Read')" in source
    assert 'GetFinalPathNameByHandle($stream.SafeFileHandle' in source
    assert "Assert-PhysicalEnrollmentRoot $root" in source
    assert '-Operation Sign' not in source
    for mutation in ('.Kill(','.Write(','.WriteAll','.Delete(','.Replace(','.CreateDirectory('):assert mutation not in source
    assert hashlib.sha256(entry.COLLECTOR.read_bytes()).hexdigest()==entry.COLLECTOR_SHA256

@pytest.mark.parametrize('damage',['none','disabled','string-enabled','integer-enabled','missing-enabled','wrong-trigger-type','wrong-action','wrong-principal'])
def test_native_taskshape_requires_enabled_typed_logon_trigger(tmp_path,damage):
    sid='S-1-5-21-1-2-3-1002';root=r'C:\Users\fixture\AppData\Local\Prometheus'
    task=dict(Actions=[dict(Execute=root+r'\Prometheus-QuietHost-v3.exe',Arguments='reconnect',WorkingDirectory=root)],Triggers=[dict(Enabled=True,CimClass=dict(CimClassName='MSFT_TaskLogonTrigger'),UserId=sid)],Principal=dict(UserId=sid,RunLevel=0,LogonType=3),Settings=dict(MultipleInstances=2,DisallowStartIfOnBatteries=False,StopIfGoingOnBatteries=False,StartWhenAvailable=True,ExecutionTimeLimit='PT0S'))
    if damage=='disabled':task['Triggers'][0]['Enabled']=False
    elif damage=='string-enabled':task['Triggers'][0]['Enabled']='true'
    elif damage=='integer-enabled':task['Triggers'][0]['Enabled']=1
    elif damage=='missing-enabled':del task['Triggers'][0]['Enabled']
    elif damage=='wrong-trigger-type':task['Triggers'][0]['CimClass']['CimClassName']='MSFT_TaskDailyTrigger'
    elif damage=='wrong-action':task['Actions'][0]['Arguments']='desktop-commander'
    elif damage=='wrong-principal':task['Principal']['UserId']='S-1-5-21-9-8-7-1002'
    data=tmp_path/'task.json';data.write_text(json.dumps(task))
    driver=tmp_path/'shape.ps1'
    driver.write_text(r'''
param([string]$Source,[string]$Fixture,[string]$Root,[string]$Sid)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($name in @('Resolve-Sid','Test-ActiveMigrationTaskShape')){
 $f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -CEQ $name)
 if($f.Count -ne 1){throw 'Expected pure task-shape function missing'};. ([scriptblock]::Create($f[0].Extent.Text))
}
$task=Get-Content -LiteralPath $Fixture -Raw|ConvertFrom-Json
@{owned_shape=(Test-ActiveMigrationTaskShape $task $Root $Sid)}|ConvertTo-Json -Compress
''')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    run=subprocess.run([str(entry.PWSH),'-NoProfile','-NonInteractive','-File',str(driver),'-Source',str(entry.COLLECTOR),'-Fixture',str(data),'-Root',root,'-Sid',sid],capture_output=True,text=True,timeout=10)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)==dict(owned_shape=damage=='none')
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before

@pytest.mark.parametrize('delta,accepted',[(-1,False),(0,True),(6,True),(9,True),(10,False)])
def test_native_creation_stamp_keeps_100ns_ticks_integer(tmp_path,delta,accepted):
    driver=tmp_path/'stamp.ps1'
    driver.write_text(r'''
param([string]$Source,[long]$Delta)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
$f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -CEQ 'Test-WorkerCreationStamp')
if($f.Count -ne 1){throw 'Stamp predicate missing'};. ([scriptblock]::Create($f[0].Extent.Text))
[long]$observed=639282293486435270
@{accepted=(Test-WorkerCreationStamp ($observed+$Delta) $observed)}|ConvertTo-Json -Compress
''')
    run=subprocess.run([str(entry.PWSH),'-NoProfile','-NonInteractive','-File',str(driver),'-Source',str(entry.COLLECTOR),'-Delta',str(delta)],capture_output=True,text=True,timeout=10)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)==dict(accepted=accepted)
