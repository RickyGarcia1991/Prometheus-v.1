param(
  [Parameter(Mandatory=$true)][string]$DestinationRoot
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repo
try {
  $full=(git rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve Git HEAD.' }
  $short=(git rev-parse --short HEAD).Trim()
  $dest=Join-Path $DestinationRoot ("Prometheus-v0.6-dev-"+$short)
  if (Test-Path $dest) { throw "Snapshot already exists: $dest" }
  New-Item -ItemType Directory -Path $dest | Out-Null
  $manifest=@()
  foreach($rel in (git ls-files)) {
    $src=Join-Path $repo $rel
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "Tracked file missing: $rel" }
    $out=Join-Path $dest $rel
    $parent=Split-Path $out -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination $out
    $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $out).Hash.ToLowerInvariant()
    $manifest += [ordered]@{path=$rel.Replace('\','/');sha256=$hash}
  }
  $doc=[ordered]@{git_commit=$full;created=(Get-Date).ToUniversalTime().ToString('o');files=$manifest}
  $doc | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $dest 'SOURCE-MANIFEST.json') -Encoding UTF8
  $bad=0
  foreach($entry in $manifest) {
    $file=Join-Path $dest $entry.path
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -ne $entry.sha256) { $bad++ }
  }
  if ($bad -ne 0) { throw "Snapshot verification failed: $bad mismatches." }
  [pscustomobject]@{destination=$dest;commit=$full;files=$manifest.Count;bad=$bad} | ConvertTo-Json
} catch {
  if ($dest -and (Test-Path $dest)) { Remove-Item -LiteralPath $dest -Recurse -Force }
  throw
} finally {
  Pop-Location
}
