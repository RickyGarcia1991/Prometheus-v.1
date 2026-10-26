$ErrorActionPreference='Stop'
$base=$PSScriptRoot
$helper=Join-Path $base 'read-controller-production-preflight.ps1'
$pins=Join-Path $base 'controller-production-preflight-pins.private.json'
$report=Join-Path $base 'controller-production-live-preflight.json'
$guards=[Collections.Generic.List[object]]::new()
try {
 foreach($f in @($helper,$pins)){$guards.Add([IO.File]::Open($f,'Open','Read','Read'))}
 if((Get-FileHash $helper).Hash.ToLower() -cne '1a4d7171fd05bb4bb4e33fb48d5d66a5a99c0877d2ed908dc4d099206ff55d57'){throw 'Preflight helper changed'}
 if((Get-FileHash $pins).Hash.ToLower() -cne 'e0f9851e4d94c0c07328d8de3f908c7f9e11400d65f60cef6a848d34d18e76a7'){throw 'Private identity pins changed'}
 $argsTable=Get-Content $pins -Raw|ConvertFrom-Json -AsHashtable
 $result=& $helper @argsTable | ConvertFrom-Json
 if($result.read_only -ne $true -or $result.activation_ready -ne $false){throw 'Unexpected preflight scope'}
 $record=@{passed=$true;captured=[DateTime]::UtcNow.ToString('o');helper_sha256='1a4d7171fd05bb4bb4e33fb48d5d66a5a99c0877d2ed908dc4d099206ff55d57';result=$result}
}catch{$record=@{passed=$false;captured=[DateTime]::UtcNow.ToString('o');error=$_.Exception.Message;read_only=$true;activation_ready=$false}}
finally{foreach($g in $guards){$g.Dispose()}}
$bytes=[Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Depth 8))
$f=[IO.File]::Open($report,'CreateNew','Write','None');try{$f.Write($bytes,0,$bytes.Length);$f.Flush($true)}finally{$f.Dispose()}
if(!$record.passed){exit 1}
