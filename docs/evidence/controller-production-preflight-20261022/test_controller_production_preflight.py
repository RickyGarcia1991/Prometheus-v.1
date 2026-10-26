import json,shutil,subprocess
import os
from pathlib import Path
import pytest
HELPER=Path(__file__).with_name('read-controller-production-preflight.ps1')
@pytest.mark.parametrize('raw,accepted,legacy',[
 ('Prometheus-Controller-v0.5.10',True,False),
 ('D:\\Prometheus-Controller-v0.5.10',True,True),
 ('E:/Prometheus-Controller-v0.5.10',True,True),
 ('D:\\nested\\Prometheus-Controller-v0.5.10',False,False),
 ('D:\\..\\Prometheus-Controller-v0.5.10',False,False),
 ('D:Prometheus-Controller-v0.5.10',False,False),
 ('\\\\server\\Prometheus-Controller-v0.5.10',False,False),
 ('D:\\Prometheus-Controller-v0.5.10\\',False,False),
 ('D:\\Prometheus-Controller-v0.5.10:stream',False,False),
 ('Prometheus-Controller-v0.5.10\n',False,False),
 ('D:\\Prometheus-Controller-v0.5.10\r\n',False,False),
 ])
def test_legacy_pointer_is_only_a_leaf_hint(tmp_path,raw,accepted,legacy):
 script=tmp_path/'pointer.ps1'
 script.write_text(r'''
param($Source,$Raw)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
$f=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq 'Convert-PreflightControllerPointer'
Invoke-Expression $f.Extent.Text
try{$p=Convert-PreflightControllerPointer $Raw;@{ok=$true;pointer=$p}|ConvertTo-Json}catch{@{ok=$false}|ConvertTo-Json}
''')
 result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),raw],capture_output=True,text=True,timeout=20)
 assert result.returncode==0,result.stderr
 parsed=json.loads(result.stdout)
 assert parsed['ok']==accepted
 if accepted:assert parsed['pointer']=={'leaf':'Prometheus-Controller-v0.5.10','legacy':legacy}
def test_native_metadata_file_and_junction_ancestor(tmp_path):
 target=tmp_path/'target';target.mkdir();(target/'metadata.json').write_text('{}')
 junction=tmp_path/'junction'
 created=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True,text=True)
 assert created.returncode==0,created.stderr
 script=tmp_path/'guard.ps1'
 script.write_text(r'''
param($Source,$Plain,$Redirected)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
$f=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq 'Assert-NoPreflightReparse'
Invoke-Expression $f.Extent.Text
Assert-NoPreflightReparse $Plain
$refused=$false
try{Assert-NoPreflightReparse $Redirected}catch{if($_.Exception.Message -notlike '*Reparse component refused*'){throw};$refused=$true}
if(!$refused){throw 'Junction ancestor accepted'}
''')
 try:
  result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),str(target/'metadata.json'),str(junction/'metadata.json')],capture_output=True,text=True,timeout=20)
  assert result.returncode==0,result.stderr
 finally:os.rmdir(junction)
@pytest.mark.parametrize('case',['valid','profile','host','size','volume','disk','label','duplicate','health','metadata','pointer','ownership','incomplete'])
def test_preflight_boundaries(tmp_path,case):
 script=tmp_path/'check.ps1'
 script.write_text(r'''
param($Source,$Case)
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$null,[ref]$null)
foreach($name in @('Get-PreflightHash','Test-ControllerPreflightSnapshot')){$f=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|Where-Object Name -eq $name;Invoke-Expression $f.Extent.Text}
$e=@{profile='C:\Users\test';host=('a'*64);bytes=2000000000000;volume='volume';disk='disk'}
$s=@{complete=$true;profile=$e.profile;host=$e.host;volumes=@(@{label='Prometheus-2TB';bytes=$e.bytes;unique='volume';disk='disk';healthy=$true});metadata_bytes=123;metadata_sha256=('b'*64);controller_release='Prometheus-Controller-v0.5.11';controllers=1;supervisors=2;ambiguous_processes=0}
switch($Case){profile{$s.profile='other'} host{$s.host='other'} size{$s.volumes[0].bytes=1} volume{$s.volumes[0].unique='other'} disk{$s.volumes[0].disk='other'} label{$s.volumes[0].label='other'} duplicate{$s.volumes+= $s.volumes[0]} health{$s.volumes[0].healthy=$false} metadata{$s.metadata_bytes=1048577} pointer{$s.controller_release='..\other'} ownership{$s.ambiguous_processes=1} incomplete{$s.complete=$false}}
try{$r=Test-ControllerPreflightSnapshot $s $e;@{ok=$true;result=$r}|ConvertTo-Json -Depth 6}catch{@{ok=$false}|ConvertTo-Json}
''')
 result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(script),str(HELPER),case],capture_output=True,text=True,timeout=20)
 assert result.returncode==0,result.stderr
 body=json.loads(result.stdout)
 assert body['ok']==(case=='valid')
 if case=='valid':
  assert body['result']['activation_ready'] is False
  assert body['result']['owned_supervisor_count']==2
  assert 'C:\\Users' not in result.stdout and 'CommandLine' not in result.stdout

def test_no_mutating_commands_or_unpinned_identity_defaults():
 text=HELPER.read_text(encoding='utf-8-sig')
 for forbidden in ('Stop-Process','Start-Process','Register-ScheduledTask','Set-Content','WriteAllBytes','Remove-Item'):
  assert forbidden not in text
 assert 'GetCurrentPackageFullName' in text
 assert 'GetFinalPathNameByHandle' in text
 assert 'GetOwnerSid' in text
 assert text.count('[Parameter(Mandatory)]')==5
