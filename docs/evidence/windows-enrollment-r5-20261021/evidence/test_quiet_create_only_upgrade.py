import json, shutil, subprocess
from pathlib import Path
import pytest
HELPER=Path(__file__).with_name('upgrade-quiet-host-create-only.ps1')
def test_native_two_module_cleanup_before_activation(tmp_path):
    for name in ('old', 'new'):
        directory=tmp_path/name
        directory.mkdir()
        (directory/'ReconnectEnrollment.psm1').write_text("function Get-FixtureIdentity { '"+name+"' }; Export-ModuleMember Get-FixtureIdentity")
    script=tmp_path/'modules.ps1'
    script.write_text(r'''
param($Source,$Root)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
$f=$ast.Find({param($a)$a -is [Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq 'Remove-UpgradeEnrollmentModules'},$false)
Invoke-Expression $f.Extent.Text
Import-Module (Join-Path $Root 'old/ReconnectEnrollment.psm1')
Import-Module (Join-Path $Root 'new/ReconnectEnrollment.psm1')
if(@(Get-Module ReconnectEnrollment -All).Count -ne 2){throw 'Fixture did not reproduce duplicate modules'}
Remove-UpgradeEnrollmentModules
if(@(Get-Module ReconnectEnrollment -All).Count -ne 0){throw 'Old modules remain'}
Import-Module (Join-Path $Root 'new/ReconnectEnrollment.psm1')
if(@(Get-Module ReconnectEnrollment).Count -ne 1 -or (Get-FixtureIdentity) -ne 'new'){throw 'Fresh activation import failed'}
''')
    result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    text=HELPER.read_text()
    assert text.index(' Remove-UpgradeEnrollmentModules\n') < text.index(' & $activation -Package')
SCRIPT=r'''
param($Source,$Case)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($f in $ast.FindAll({param($a)$a -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){Invoke-Expression $f.Extent.Text}
$script:events=[Collections.Generic.List[string]]::new();$script:task='old';$script:binary='old';$script:backup=$false
$p=@{
 Check={if($Case -eq 'preflight'){throw 'preflight'};@{old_hash='expected';task_xml='old exact disabled'}}
 Save={param($phase,$r)$script:events.Add($phase);if($Case -eq ('save-'+$phase)){throw 'save fault'}}
 Backup={param($r)if($Case -eq 'backup'){throw 'backup failure'};$script:backup=$true}
 Recheck={param($r)if($Case -eq 'race'){throw 'ownership changed'}}
 RemoveTask={if($Case -eq 'task'){throw 'foreign task preserved'};$script:task=$null}
 RemoveBinary={param($r)if($Case -eq 'binary'){throw 'changed file preserved'};$script:binary=$null}
 Enroll={if($Case -eq 'enroll'){throw 'new enrollment failed'};$script:task='fresh';$script:binary='new';[pscustomobject]@{status='enrolled-disabled';activated=$false;id=$(if($Case -eq 'old-id'){'76285c9134d2434790cb84b4680350b4'}else{'1'*32})}}
}
$ok=$false;try{Invoke-QuietHostCreateOnlyUpgrade $p|Out-Null;$ok=$true}catch{}
@{ok=$ok;task=$script:task;binary=$script:binary;backup=$script:backup;events=@($script:events)}|ConvertTo-Json -Compress
'''
@pytest.mark.parametrize('case',['success','preflight','backup','race','task','binary','enroll','old-id','save-intent','save-backup-verified','save-old-task-removed','save-old-payloads-removed','save-new-enrolled-disabled'])
def test_upgrade_transaction_faults_preserve_recovery_evidence(tmp_path,case):
    script=tmp_path/'test.ps1';script.write_text(SCRIPT)
    r=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),case],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    result=json.loads(r.stdout)
    if case=='success':
        assert result['ok'] and result['task']=='fresh' and result['binary']=='new' and result['backup']
        assert result['events']==['intent','backup-verified','old-task-removed','old-payloads-removed','new-enrolled-disabled']
    else:
        assert not result['ok'] and result['events'][-1]=='failed'
        if case in ('preflight','backup','race','task','save-intent','save-backup-verified'):
            assert result['task']=='old' and result['binary']=='old'
        if case in ('binary','save-old-task-removed'):
            assert result['task'] is None and result['binary']=='old' and result['backup']
        if case in ('enroll','save-old-payloads-removed'):
            assert result['task'] is None and result['binary'] is None and result['backup']
        if case in ('old-id','save-new-enrolled-disabled'):
            assert result['backup']

def test_upgrade_binding_and_no_old_identity_reuse():
    source=HELPER.read_text(encoding='utf-8-sig')
    for text in ('Read-EnrollmentJournal','Assert-PhysicalEnrollmentRoot','GetCurrentPackageFullName','GetFinalPathNameByHandle','Start-ReconnectEnrollment -Package $new','-TransactionId $result.id','b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e'):
        assert text in source
    assert 'File]::Replace' not in source
    assert 'Register-ScheduledTask' not in source
    assert 'Stop-Process' not in source
    assert 'Remove-Item' not in source


@pytest.mark.parametrize('case',['valid','wrong-phase','wrong-package','duplicate','incomplete'])
def test_old_sealed_payload_guard(tmp_path,case):
    script=tmp_path/'journal.ps1'
    script.write_text(r'''
param($Source,$Case)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($f in $ast.FindAll({param($a)$a -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){Invoke-Expression $f.Extent.Text}
$id='1'*32;$hash='a'*64
$v=[pscustomobject]@{Identity=('b'*64);Files=@([pscustomobject]@{Name='f';Hash=$hash})}
$j=[pscustomobject]@{id=$id;package_identity=$v.Identity;phase='enrolled-disabled';task_was_absent=$true;task_enabled=$false;created_files=@([pscustomobject]@{name='f';hash=$hash});completed_files=@([pscustomobject]@{name='f';hash=$hash});rollback_errors=@()}
if($Case -eq 'wrong-phase'){$j.phase='registering'}
if($Case -eq 'wrong-package'){$j.package_identity='c'*64}
if($Case -eq 'duplicate'){$j.created_files+= $j.created_files[0];$j.completed_files+= $j.completed_files[0]}
if($Case -eq 'incomplete'){$j.completed_files=@()}
$ok=$false;try{Assert-OldEnrollmentJournal $j $v $id;$ok=$true}catch{}
if($ok -ne ($Case -eq 'valid')){throw 'Unexpected journal acceptance'}
'pass'
''')
    result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='pass'

def test_physical_root_handle_gate_on_generated_directory(tmp_path):
    script=tmp_path/'physical.ps1'
    script.write_text(r'''
param($Source,$Root)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($f in $ast.FindAll({param($a)$a -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){Invoke-Expression $f.Extent.Text}
try{$h=Assert-PhysicalEnrollmentRoot $Root;$h.Dispose();'physical'}catch{if($_.Exception.Message -eq 'Packaged/MSIX execution context refused'){'packaged-refused'}else{throw}}
''')
    result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip() in ('physical','packaged-refused')

