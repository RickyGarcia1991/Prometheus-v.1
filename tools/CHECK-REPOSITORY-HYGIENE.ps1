$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repo
try {
  $tracked=@(git ls-files)
  $forbidden='(?i)(^|/)(memory\.sqlite3|\.env|id_rsa|id_ed25519|credentials\.json)$|\.(zim|gguf|bin)$'
  $bad=@($tracked | Where-Object { $_ -match $forbidden })
  if($bad.Count){ throw ('Forbidden tracked payload: '+($bad -join ', ')) }
  $patterns=@('sk-proj-[A-Za-z0-9_-]{20,}','AKIA[0-9A-Z]{16}','-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----')
  foreach($file in $tracked){
    if(-not (Test-Path -LiteralPath $file -PathType Leaf)){ continue }
    if((Get-Item -LiteralPath $file).Length -gt 2MB){ continue }
    $text=Get-Content -Raw -ErrorAction SilentlyContinue -LiteralPath $file
    foreach($pattern in $patterns){
      if($text -match $pattern){ throw "Possible secret in tracked file: $file" }
    }
  }
  Write-Output "PASS repository hygiene: $($tracked.Count) tracked files"
} finally { Pop-Location }
