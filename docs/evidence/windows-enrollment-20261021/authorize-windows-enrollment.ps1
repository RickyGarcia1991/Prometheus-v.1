param([Parameter(Mandatory)][string]$Package,[Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ManifestHash)
$ErrorActionPreference='Stop'
$out=Join-Path $PSScriptRoot 'windows-authorization-result.json'
$moduleLock=$null;$qualifierLock=$null
try {
 $qualifier=Join-Path $PSScriptRoot 'qualify-reconnect-enrollment.ps1'
 $qualifierLock=[IO.File]::Open($qualifier,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 if((Get-FileHash -LiteralPath $qualifier -Algorithm SHA256).Hash.ToLower() -cne 'd38b175e0b3ef0f54270609078adecbbe61c64f934b790136ab12efcbedfc3c0'){throw 'Qualification helper changed'}
 $moduleLock=[IO.File]::Open((Join-Path $Package 'ReconnectEnrollment.psm1'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 & (Join-Path $PSScriptRoot 'qualify-reconnect-enrollment.ps1') -Operation Verify -PackageKind ReconnectEnrollment -Candidate $Package -ExpectedManifestSha256 $ManifestHash | Out-Null
 Import-Module (Join-Path $Package 'ReconnectEnrollment.psm1') -Force
 $result=Initialize-ReconnectAuthorization -Package $Package
 [pscustomobject]@{success=$true;authorization=$result;manifest_sha256=$ManifestHash;enrollment_started=$false;activated=$false;completed_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $out -Encoding utf8
} catch {
 [pscustomobject]@{success=$false;error=$_.Exception.Message;manifest_sha256=$ManifestHash;activated=$false;completed_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $out -Encoding utf8
 exit 1
}
finally { if($moduleLock){$moduleLock.Dispose()}; if($qualifierLock){$qualifierLock.Dispose()} }
