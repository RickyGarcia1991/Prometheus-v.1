$ErrorActionPreference='Stop'
$p=Join-Path $PSScriptRoot 'activate-enrolled-reconnect.ps1'
$h=[IO.File]::Open($p,'Open','Read','Read')
try{
 if((Get-FileHash $p).Hash.ToLower() -cne '5bd020db3ca7ded6c8ab4a52eb168cc904ba28b1f050076a17e34e642dda9a97'){throw 'Activation helper changed'}
 & $p -Package (Join-Path $PSScriptRoot 'reconnect-enrollment-candidate-r5') -ManifestHash b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e -TransactionId 7b06d1cde83a4b8ebb5a8ac1fa086887 | Set-Content (Join-Path $PSScriptRoot 'real-r5-activation-result.json')
}finally{$h.Dispose()}
