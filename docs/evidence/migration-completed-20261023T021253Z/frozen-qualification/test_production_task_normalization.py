"""Pure XML normalization in PS7; imports module, never observes/mutates a task."""
import json,subprocess
from pathlib import Path
import pytest

B=Path(__file__).resolve().parent
PS=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'

def test_task_enabled_default_normalization_and_exact_other_content():
    code=r'''
$ErrorActionPreference='Stop'
Import-Module ./ActiveHostMigration.Native.psm1 -Force
$ns='http://schemas.microsoft.com/windows/2004/02/mit/task'
$base='<Task xmlns="'+$ns+'"><Settings><AllowStartOnDemand>true</AllowStartOnDemand></Settings><Actions><Exec><Command>fixed command</Command></Exec></Actions><Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers></Task>'
$n=Get-MigrationNormalizedTaskXml $base
foreach($flag in @('true','false')){
 $variant=$base.Replace('<Settings>','<Settings><Enabled>'+$flag+'</Enabled>')
 if((Get-MigrationNormalizedTaskXml $variant) -cne $n){throw 'Optional Enabled normalization mismatch'}
}
if((Get-MigrationNormalizedTaskXml ($base.Replace('><',">`n  <"))) -cne $n){throw 'Formatting changed digest'}
foreach($variant in @($base.Replace('fixed command','different command'),$base.Replace('LogonTrigger><Enabled>true','LogonTrigger><Enabled>false'),$base.Replace('AllowStartOnDemand>true','AllowStartOnDemand>false'))){if((Get-MigrationNormalizedTaskXml $variant) -ceq $n){throw 'Other content was normalized away'}}
$bad=@($base.Replace('<Settings>','<Settings><Enabled>true</Enabled><Enabled>false</Enabled>'),$base.Replace('<Settings>','<Settings><Enabled xmlns="urn:wrong">true</Enabled>'),$base.Replace('<Settings>','<Settings><Enabled>invalid</Enabled>'),$base.Replace($ns,'urn:wrong'))
foreach($xml in $bad){$refused=$false;try{$null=Get-MigrationNormalizedTaskXml $xml}catch{$refused=$true};if(!$refused){throw 'Malformed scope accepted'}}
$actual=Join-Path $PWD 'production-task-default-enabled.private.xml'
$actualTested=$false
if(Test-Path -LiteralPath $actual){
 $text=[IO.File]::ReadAllText($actual);$baseline=Get-MigrationNormalizedTaskXml $text
 foreach($flag in @('true','false')){
  $doc=[xml]$text;$settings=$doc.SelectSingleNode('/*[local-name()="Task"]/*[local-name()="Settings"]')
  $child=$doc.CreateElement('Enabled',$ns);$child.InnerText=$flag;$null=$settings.AppendChild($child)
  if((Get-MigrationNormalizedTaskXml $doc.OuterXml) -cne $baseline){throw 'Actual optional Enabled export mismatch'}
 }
 $actualTested=$true
}
@{passed=$true;actual_export_tested=$actualTested}|ConvertTo-Json -Compress
'''
    result=subprocess.run([str(PS),'-NoProfile','-NonInteractive','-Command',code],cwd=B,capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['passed']
