param([Parameter(Mandatory)][string]$Package,[Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestHash)
$ErrorActionPreference='Stop'
$out=Join-Path $PSScriptRoot 'windows-recovery-result.json'
$moduleLock=$null;$qualifierLock=$null
try {
 $qualifier=Join-Path $PSScriptRoot 'qualify-reconnect-enrollment-v2.ps1'
 $qualifierLock=[IO.File]::Open($qualifier,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 if((Get-FileHash -LiteralPath $qualifier -Algorithm SHA256).Hash.ToLower() -cne '9e46ac586172efcf43e82ce0dab9b56c888cf967c614e27ba5214210b2daa0fe'){throw 'Qualification helper changed'}
 $moduleLock=[IO.File]::Open((Join-Path $Package 'ReconnectEnrollment.psm1'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 & (Join-Path $PSScriptRoot 'qualify-reconnect-enrollment-v2.ps1') -Operation Verify -PackageKind ReconnectEnrollment -Candidate $Package -ExpectedManifestSha256 $ManifestHash | Out-Null
 Import-Module (Join-Path $Package 'ReconnectEnrollment.psm1') -Force
 $result=Repair-ReconnectEnrollment -Package $Package -TransactionId '1b06096c52914b72afdb4663c468ff0b'
 [pscustomobject]@{success=$true;recovery=$result;manifest_sha256=$ManifestHash;enrollment_started=$false;activated=$false;completed_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $out -Encoding utf8
} catch {
 [pscustomobject]@{success=$false;error=$_.Exception.Message;manifest_sha256=$ManifestHash;activated=$false;completed_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $out -Encoding utf8
 exit 1
}
finally { if($moduleLock){$moduleLock.Dispose()}; if($qualifierLock){$qualifierLock.Dispose()} }
