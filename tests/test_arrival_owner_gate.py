"""Native owner-gate tests: authentication stays required on arrival."""
import json
import os
from pathlib import Path
import subprocess
import pytest

SOURCE=Path(__file__).resolve().parents[1]/'tools/Prometheus.HostAgent/Prometheus-Arrival.ps1'
PS=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'

@pytest.mark.skipif(os.name!='nt', reason='Native Windows arrival gate')
@pytest.mark.parametrize('access,protected,expected',[
 ({'required':True,'ready':True,'unlocked':False},'valid','awaiting-owner-unlock'),
 ({'required':True,'ready':True,'unlocked':True},'valid','ready'),
 ({'required':False,'ready':True,'unlocked':True},'valid','ready'),
 ({'required':True,'ready':False,'unlocked':False},'valid','error'),
 ({'required':'true','ready':True,'unlocked':False},'valid','error'),
 ({'required':True,'ready':True},'valid','error'),
 (None,'valid','error'),
 ({'required':True,'ready':True,'unlocked':True},'closing','error'),
 ({'required':True,'ready':True,'unlocked':True},'denied','error'),
])
def test_arrival_owner_gate(tmp_path,access,protected,expected):
    fixture=json.dumps({'owner_access':access,'token':'fixture-token'})
    script=tmp_path/'gate.ps1'
    script.write_text("""$ErrorActionPreference='Stop'
$t=$null;$e=$null
$a=[Management.Automation.Language.Parser]::ParseFile('__SOURCE__',[ref]$t,[ref]$e)
if($e.Count){throw 'Source parse failure'}
$f=$a.Find({param($x)$x -is [Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq 'Get-ArrivalReadiness'},$true)
Invoke-Expression $f.Extent.Text
$script:calls=0
function Invoke-RestMethod($Uri,$Headers,$TimeoutSec){
 if($Uri.EndsWith('/api/bootstrap')){return ('__FIXTURE__'|ConvertFrom-Json)}
 $script:calls++
 if($Headers['X-Prometheus-Token'] -cne 'fixture-token'){throw 'Token not forwarded'}
 if('__PROTECTED__' -eq 'denied'){throw 'Unauthorized'}
 return @{closing=('__PROTECTED__' -eq 'closing');saved_locally=$true;hardware=@{chat_ready=$false}}
}
try{$r=Get-ArrivalReadiness 'http://127.0.0.1:54555';@{phase=$r.Phase;calls=$script:calls}|ConvertTo-Json -Compress}
catch{@{phase='error';calls=$script:calls}|ConvertTo-Json -Compress}
""".replace('__SOURCE__',str(SOURCE).replace("'","''")).replace('__FIXTURE__',fixture).replace('__PROTECTED__',protected),encoding='utf-8')
    p=subprocess.run([str(PS),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    result=json.loads(p.stdout)
    assert result['phase']==expected
    if expected=='awaiting-owner-unlock' or access is None or not isinstance(access.get('required'),bool) or not access.get('ready') or 'unlocked' not in access:
        assert result['calls']==0
    else:
        assert result['calls']==1
