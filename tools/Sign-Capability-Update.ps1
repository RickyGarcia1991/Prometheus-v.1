param(
    [Parameter(Mandatory=$true)][string]$Bundle,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedBundleSha256,
    [ValidateSet('Validate','Sign')][string]$Mode='Validate',
    [string]$Output
)
# Signing-only handoff. There is intentionally no SSD activation or key creation.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$trusted='898F702114B0F889763589C4057DC19CF1657CD5'
$verifierHash='710d37bf2eb0318e9c20835a9deff5942377cb316c1b5d3bd934be6810419f53'
$release='Prometheus-v0.8.0-dev3-main-workstation-20261010'

function AssertPlain([string]$Path){
    $cursor=[IO.Path]::GetFullPath($Path)
    while($cursor){
        if(Test-Path -LiteralPath $cursor){
            if((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Linked paths are refused'}
        }
        $parent=[IO.Path]::GetDirectoryName($cursor)
        if($parent -eq $cursor){break}
        $cursor=$parent
    }
}
function SafePath([string]$Base,[string]$Relative){
    if(!$Relative -or $Relative -match '[\\:\x00-\x1f]' -or [IO.Path]::IsPathRooted($Relative)){throw 'Unsafe manifest path'}
    foreach($part in ($Relative -split '/')){
        if(!$part -or $part -in @('.','..') -or $part -match '[. ]$|^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)'){throw 'Unsafe manifest component'}
    }
    $basePath=[IO.Path]::GetFullPath($Base).TrimEnd('\','/')
    $path=[IO.Path]::GetFullPath((Join-Path $basePath $Relative))
    if(!$path.StartsWith($basePath+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Manifest escapes candidate'}
    AssertPlain $path
    return $path
}
function AssertHash([string]$Path,[string]$Hash){
    AssertPlain $Path
    if(!(Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash){throw ('Hash verification failed: '+$Path)}
}
function ValidateCandidate([string]$Candidate,[string]$ManifestHash){
    $manifestPath=SafePath $Candidate 'SHA256-MANIFEST.json'
    if(!(Test-Path -LiteralPath $manifestPath -PathType Leaf) -or (Get-Item -LiteralPath $manifestPath).Length -gt 1000000){throw 'Missing or oversized candidate manifest'}
    AssertHash $manifestPath $ManifestHash
    $rows=@(Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json)
    if(!$rows.Count -or $rows.Count -gt 4096){throw 'Invalid candidate manifest size'}
    $expected=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    [long]$total=0
    foreach($row in $rows){
        $name=[string]$row.path
        $path=SafePath $Candidate $name
        if(!$expected.Add($name) -or $name -in @('SHA256-MANIFEST.json','SHA256-MANIFEST.sig','MANIFEST-PUBLIC.cer')){throw 'Duplicate or reserved manifest entry'}
        if($name -match '(^|/)(data|memory|\.git|\.ollama)(/|$)|\.sqlite3?$|\.db$'){throw 'Protected data cannot be part of a core update'}
        if($row.sha256 -notmatch '^[a-f0-9]{64}$' -or !($row.size_bytes -is [int] -or $row.size_bytes -is [long]) -or $row.size_bytes -lt 0 -or $row.size_bytes -gt 67108864){throw 'Invalid manifest hash or size'}
        if(!(Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -ne $row.size_bytes){throw ('Candidate size mismatch: '+$name)}
        $total+=$row.size_bytes
        if($total -gt 536870912){throw 'Candidate exceeds its size limit'}
        AssertHash $path $row.sha256
    }
    foreach($required in @('prometheus.py','src/prometheus_assistant/cli.py','tools/Verify-Repair-Core.ps1')){
        if(!$expected.Contains($required)){throw ('Missing required core file: '+$required)}
    }
    $null=$expected.Add('SHA256-MANIFEST.json')
    $queue=New-Object 'System.Collections.Generic.Queue[string]'
    $queue.Enqueue($Candidate)
    $count=0
    while($queue.Count){
        foreach($item in (Get-ChildItem -LiteralPath $queue.Dequeue() -Force)){
            $count++
            if($count -gt 8192){throw 'Candidate tree exceeds its scan limit'}
            AssertPlain $item.FullName
            if($item.PSIsContainer){$queue.Enqueue($item.FullName)}else{
                $relative=$item.FullName.Substring($Candidate.TrimEnd('\','/').Length+1).Replace('\','/')
                if(!$expected.Contains($relative)){throw ('Unsigned extra or stale signature file: '+$relative)}
            }
        }
    }
    AssertHash (SafePath $Candidate 'tools/Verify-Repair-Core.ps1') $verifierHash
    $provenance=Get-Content -LiteralPath (SafePath $Candidate 'RELEASE-PROVENANCE.json') -Raw | ConvertFrom-Json
    if($provenance.release -ne $release -or $provenance.version -ne '0.8.0.dev3'){throw 'Candidate provenance does not match this update'}
    return $rows
}

try{
    $Bundle=[IO.Path]::GetFullPath($Bundle)
    AssertPlain $Bundle
    $description=SafePath $Bundle 'UPDATE-BUNDLE.json'
    if(!(Test-Path -LiteralPath $description -PathType Leaf) -or (Get-Item -LiteralPath $description).Length -gt 1000000){throw 'Missing or oversized bundle description'}
    AssertHash $description $ExpectedBundleSha256
    $meta=Get-Content -LiteralPath $description -Raw | ConvertFrom-Json
    if($meta.schema_version -ne 1 -or $meta.state -ne 'unsigned_candidate_activation_pending' -or $meta.release -ne $release -or $meta.version -ne '0.8.0.dev3' -or $meta.base_release -ne 'Prometheus-v0.8.0-dev1-remote-20261010'){throw 'Unexpected bundle identity or state'}
    if($meta.required_signer_thumbprint -ne $trusted -or $meta.verifier_sha256 -ne $verifierHash){throw 'The bundle attempts to change the pinned trust base'}
    $candidate=SafePath $Bundle $release
    $rows=@(ValidateCandidate $candidate $meta.candidate_manifest_sha256)
    if($rows.Count -ne $meta.file_count){throw 'Bundle file count mismatch'}
    if($Mode -eq 'Validate'){
        [pscustomobject]@{checksums_verified=$true;signature_verified=$false;files=$rows.Count;activation='pending';writes_performed=$false}|ConvertTo-Json -Compress
        exit 0
    }
    if(!$Output){throw 'Sign requires a new, separate Output directory'}
    $Output=[IO.Path]::GetFullPath($Output).TrimEnd('\','/')
    AssertPlain $Output
    if((Test-Path -LiteralPath $Output) -or $Output.StartsWith($Bundle.TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or $Output -eq $Bundle){throw 'Signing output must be new and outside the unsigned bundle'}
    # Never generate, import, export, or replace a private key.
    $cert=Get-Item -LiteralPath ('Cert:\CurrentUser\My\'+$trusted) -ErrorAction SilentlyContinue
    if(!$cert -or !$cert.HasPrivateKey){throw 'Original pinned signing identity with private key is unavailable in the current user certificate store; signing and activation remain pending'}
    $now=Get-Date
    if($now -lt $cert.NotBefore -or $now -gt $cert.NotAfter){throw 'Pinned certificate is outside its validity period; do not replace the trust identity automatically'}
    $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    if(!$rsa){throw 'Original signing identity does not expose its RSA private key'}
    try{
        [IO.Directory]::CreateDirectory($Output)|Out-Null
        foreach($row in $rows){
            $source=SafePath $candidate ([string]$row.path)
            $target=SafePath $Output ([string]$row.path)
            [IO.Directory]::CreateDirectory((Split-Path $target -Parent))|Out-Null
            [IO.File]::Copy($source,$target,$false)
        }
        [IO.File]::Copy((SafePath $candidate 'SHA256-MANIFEST.json'),(SafePath $Output 'SHA256-MANIFEST.json'),$false)
        # Check the copy again before signing, including files changed mid-copy.
        $null=ValidateCandidate $Output $meta.candidate_manifest_sha256
        $manifestBytes=[IO.File]::ReadAllBytes((SafePath $Output 'SHA256-MANIFEST.json'))
        $signature=$rsa.SignData($manifestBytes,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
        [IO.File]::WriteAllBytes((SafePath $Output 'MANIFEST-PUBLIC.cer'),$cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
        [IO.File]::WriteAllBytes((SafePath $Output 'SHA256-MANIFEST.sig'),$signature)
    }finally{$rsa.Dispose()}
    $engine=(Get-Process -Id $PID).Path
    $result=& $engine -NoProfile -File (SafePath $Output 'tools/Verify-Repair-Core.ps1') -Root $Output
    if($LASTEXITCODE -ne 0){throw 'Signed core verification failed; retain the output for inspection and do not activate it'}
    $verified=$result|ConvertFrom-Json
    if(!$verified.signature_verified -or !$verified.healthy){throw 'Signed candidate was not confirmed healthy'}
    [pscustomobject]@{checksums_verified=$true;signature_verified=$true;files=$rows.Count;signer=$trusted;output=$Output;activation='pending';ssd_changed=$false}|ConvertTo-Json -Compress
    exit 0
}catch{
    Write-Error ('Capability update stopped: '+$_.Exception.Message) -ErrorAction Continue
    exit 2
}
