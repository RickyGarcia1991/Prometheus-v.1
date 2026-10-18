param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$identity=$null; $certificate=$null; $rsa=$null
function Assert-Plain([string]$Path) {
    $item=[IO.Path]::GetFullPath($Path)
    while ($item) {
        if (Test-Path -LiteralPath $item) {
            if ((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked path.' }
        }
        $item=[IO.Path]::GetDirectoryName($item)
    }
}
function Hash-Bytes([byte[]]$Bytes) { return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant() }
function Assert-Fields($Value,[string[]]$Names) {
    if (!$Value -or (@($Value.PSObject.Properties.Name | Sort-Object) -join ',') -ne (@($Names | Sort-Object) -join ',')) { throw 'Invalid fields.' }
}
function Assert-JsonFields($Value,[string[]]$Names) {
    if ($Value.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { throw 'Invalid JSON object.' }
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($property in $Value.EnumerateObject()) {
        if (!$seen.Add($property.Name)) { throw 'Duplicate JSON field.' }
    }
    $expected=[Collections.Generic.HashSet[string]]::new($Names,[StringComparer]::Ordinal)
    if (!$seen.SetEquals($expected)) { throw 'JSON field names must match exactly.' }
}
function Assert-LintTool($Tool) {
    Assert-Fields $Tool @('sha256','version')
    if ($Tool.sha256 -isnot [string] -or $Tool.sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        $Tool.version -isnot [string] -or $Tool.version -cnotmatch '\A[0-9]{1,3}\.[0-9]{1,4}\.[0-9]{1,4}\z') { throw 'Invalid lint tool pin.' }
}
function Assert-LintRequest($Document,[byte[]]$Bytes) {
    $names=@('schema_version','job_id','device_id','worker_digest','operation','timeout_seconds','files','request_digest','tool','profile')
    $json=[System.Text.Json.JsonDocument]::Parse([Text.Encoding]::UTF8.GetString($Bytes))
    try {
        Assert-JsonFields $json.RootElement @('schema_version','purpose','issued_at','expires_at','request')
        $request=$json.RootElement.GetProperty('request')
        Assert-JsonFields $request $names
        Assert-JsonFields $request.GetProperty('tool') @('sha256','version')
        $files=$request.GetProperty('files')
        if ($files.ValueKind -ne [System.Text.Json.JsonValueKind]::Array -or $files.GetArrayLength() -lt 1 -or $files.GetArrayLength() -gt 8) { throw 'Invalid lint files.' }
        foreach ($file in $files.EnumerateArray()) { Assert-JsonFields $file @('path','content') }
    } finally { $json.Dispose() }
    $request=$Document.request
    Assert-LintTool $request.tool
    if ($Document.schema_version -isnot [long] -or $Document.schema_version -ne 2 -or
        $Document.purpose -isnot [string] -or $Document.purpose -cne 'prometheus.remote.lint' -or
        $request.schema_version -isnot [long] -or $request.schema_version -ne 2 -or
        $request.operation -isnot [string] -or $request.operation -cne 'python-ruff-check-v1' -or
        $request.profile -isnot [string] -or $request.profile -cne 'ruff-python-v1' -or
        $request.job_id -isnot [string] -or $request.job_id -cnotmatch '\A[0-9a-f]{32}\z' -or
        $request.worker_digest -isnot [string] -or $request.worker_digest -cnotmatch '\A[0-9a-f]{64}\z' -or
        $request.request_digest -isnot [string] -or $request.request_digest -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Invalid lint signing contract.' }
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $total=0
    $utf8=[Text.UTF8Encoding]::new($false,$true)
    foreach ($file in $request.files) {
        if ($file.path -isnot [string] -or $file.path.Length -lt 1 -or $file.path.Length -gt 240 -or
            !$file.path.EndsWith('.py',[StringComparison]::Ordinal) -or !$seen.Add($file.path) -or
            $file.content -isnot [string] -or $file.content.Length -gt 256000) { throw 'Invalid lint source.' }
        foreach ($part in $file.path.Split('/')) {
            if ($part -cnotmatch '\A[A-Za-z0-9_][A-Za-z0-9_.-]*\z' -or $part.EndsWith('.') -or
                $part -imatch '\A(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|\z)' -or
                $part.ToLowerInvariant() -in @('__pycache__','node_modules','site-packages')) { throw 'Invalid lint source path.' }
        }
        $size=$utf8.GetByteCount($file.content); $total+=$size
        if ($size -gt 256000 -or $total -gt 512000) { throw 'Oversized lint source.' }
    }
}
function Assert-LintReceipt($Document,[byte[]]$Bytes) {
    $json=[System.Text.Json.JsonDocument]::Parse([Text.Encoding]::UTF8.GetString($Bytes))
    try {
        Assert-JsonFields $json.RootElement @('schema_version','purpose','issued_at','expires_at','request_digest','receipt')
        $receipt=$json.RootElement.GetProperty('receipt')
        Assert-JsonFields $receipt @('schema_version','job_id','device_id','worker_digest','request_digest','state','checks','operation','tool','profile','status','diagnostics','reason','source_unchanged')
        Assert-JsonFields $receipt.GetProperty('tool') @('sha256','version')
        foreach ($check in $receipt.GetProperty('checks').EnumerateArray()) { Assert-JsonFields $check @('path','passed') }
        foreach ($diagnostic in $receipt.GetProperty('diagnostics').EnumerateArray()) { Assert-JsonFields $diagnostic @('path','code','message','line','column','end_line','end_column') }
    } finally { $json.Dispose() }
    Assert-LintTool $Document.receipt.tool
    if ($Document.schema_version -isnot [long] -or $Document.schema_version -ne 2 -or
        $Document.purpose -isnot [string] -or $Document.purpose -cne 'prometheus.remote.lint.receipt' -or
        $Document.receipt.schema_version -isnot [long] -or $Document.receipt.schema_version -ne 2 -or
        $Document.receipt.operation -isnot [string] -or $Document.receipt.operation -cne 'python-ruff-check-v1' -or
        $Document.receipt.profile -isnot [string] -or $Document.receipt.profile -cne 'ruff-python-v1' -or
        $Document.receipt.request_digest -cne $Document.request_digest) { throw 'Invalid lint receipt purpose or binding.' }
}
function Assert-ResourcePayload($Document,[string]$Purpose,[byte[]]$Bytes) {
    if ($Bytes.Length -ge 8192) { throw 'Oversized resource payload.' }
    $names=@('schema_version','purpose','nonce','device_id','worker_digest','issued_at','expires_at')
    if ($Purpose -eq 'prometheus.remote.resources.advertisement') { $names+=@('observations') }
    Assert-Fields $Document $names
    # ConvertFrom-Json collapses duplicate keys, so inspect the original JSON as
    # well. Resource payloads contain only these two bounded object levels.
    $json=[System.Text.Json.JsonDocument]::Parse([Text.Encoding]::UTF8.GetString($Bytes))
    try {
        $objects=@($json.RootElement)
        if ($Purpose -eq 'prometheus.remote.resources.advertisement') { $objects+=@($json.RootElement.GetProperty('observations')) }
        foreach ($object in $objects) {
            if ($object.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { throw 'Invalid resource object.' }
            $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($property in $object.EnumerateObject()) {
                if (!$seen.Add($property.Name)) { throw 'Duplicate resource field.' }
            }
        }
        $expected=[Collections.Generic.HashSet[string]]::new([string[]]$names,[StringComparer]::Ordinal)
        $actual=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($property in $json.RootElement.EnumerateObject()) { [void]$actual.Add($property.Name) }
        if (!$actual.SetEquals($expected)) { throw 'Resource field names must match exactly.' }
        if ($Purpose -eq 'prometheus.remote.resources.advertisement') {
            $expected=[Collections.Generic.HashSet[string]]::new([string[]]@('enabled','available_ram_gib','free_disk_gib','cpu_percent','battery_percent','plugged_in'),[StringComparer]::Ordinal)
            if (!$seen.SetEquals($expected)) { throw 'Resource observation names must match exactly.' }
        }
    } finally { $json.Dispose() }
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($Document.schema_version -isnot [long] -or $Document.schema_version -ne 1 -or
        $Document.purpose -cne $Purpose -or $Document.nonce -isnot [string] -or
        $Document.nonce -cnotmatch '\A[0-9a-f]{32}\z' -or $Document.worker_digest -isnot [string] -or
        $Document.worker_digest -cnotmatch '\A[0-9a-f]{64}\z' -or $Document.device_id -isnot [string] -or
        $Document.device_id -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z' -or
        $Document.issued_at -isnot [long] -or $Document.expires_at -isnot [long] -or
        $Document.issued_at -gt $now -or $Document.expires_at -le $now -or
        $Document.expires_at-$Document.issued_at -lt 1 -or
        $Document.expires_at-$Document.issued_at -gt 30) { throw 'Invalid resource purpose, target or freshness.' }
    if ($Purpose -eq 'prometheus.remote.resources.advertisement') {
        $facts=$Document.observations
        Assert-Fields $facts @('enabled','available_ram_gib','free_disk_gib','cpu_percent','battery_percent','plugged_in')
        if ($facts.enabled -isnot [bool] -or ($null -ne $facts.plugged_in -and $facts.plugged_in -isnot [bool])) { throw 'Invalid resource switches.' }
        foreach ($name in @('available_ram_gib','free_disk_gib','cpu_percent','battery_percent')) {
            $value=$facts.$name
            if ($null -eq $value) { continue }
            if (($value -isnot [long] -and $value -isnot [double]) -or
                ![double]::IsFinite([double]$value) -or $value -lt 0 -or
                ($name -in @('cpu_percent','battery_percent') -and $value -gt 100)) { throw 'Invalid resource observation.' }
        }
    }
}
try {
    Assert-Plain $InputPath; Assert-Plain $OutputPath
    if ((Get-Item -LiteralPath $InputPath).Length -gt 4200000) { throw 'Oversized input.' }
    $inputData=[IO.File]::ReadAllText($InputPath) | ConvertFrom-Json -Depth 16
    $payload=[Convert]::FromBase64String($inputData.payload)
    if ($payload.Length -gt 3100000) { throw 'Oversized payload.' }
    $document=[Text.Encoding]::UTF8.GetString($payload) | ConvertFrom-Json -Depth 16
    if ($inputData.action -in @('sign','sign_resources')) {
        Assert-Fields $inputData @('action','payload','library_path','library_sha256','certificate_sha256')
        if ($inputData.action -eq 'sign_resources') {
            Assert-ResourcePayload $document 'prometheus.remote.resources.query' $payload
        } else {
        Assert-Fields $document @('schema_version','purpose','issued_at','expires_at','request')
        $lint=$document.purpose -ceq 'prometheus.remote.lint'
        if ($lint) { Assert-LintRequest $document $payload }
        else { Assert-Fields $document.request @('schema_version','job_id','device_id','worker_digest','operation','timeout_seconds','files','request_digest') }
        $now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        if ((!$lint -and ($document.schema_version -isnot [long] -or $document.schema_version -ne 1 -or
            $document.purpose -cne 'prometheus.remote.syntax')) -or
            $document.issued_at -isnot [long] -or $document.expires_at -isnot [long] -or
            $document.issued_at -gt $now -or $document.expires_at -le $now -or
            $document.expires_at-$document.issued_at -gt 300) { throw 'Invalid signing purpose or freshness.' }
        if ((!$lint -and ($document.request.schema_version -isnot [long] -or $document.request.schema_version -ne 1 -or
            $document.request.operation -cne 'python-syntax-check')) -or
            $document.request.device_id -isnot [string] -or
            $document.request.device_id -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z' -or
            $document.request.timeout_seconds -isnot [long] -or $document.request.timeout_seconds -lt 1 -or
            $document.request.timeout_seconds -gt 30 -or $document.request.files.Count -lt 1 -or
            $document.request.files.Count -gt 8) { throw 'Invalid target operation.' }
        }
        Assert-Plain $inputData.library_path
        if ((Get-Item -LiteralPath $inputData.library_path).Length -gt 262144 -or
            (Hash-Bytes ([IO.File]::ReadAllBytes($inputData.library_path))) -cne $inputData.library_sha256) { throw 'Library pin mismatch.' }
        . $inputData.library_path
        $identity=Read-PrometheusSigningIdentity
        if ((Hash-Bytes $identity.Certificate.RawData) -cne $inputData.certificate_sha256) { throw 'Controller certificate mismatch.' }
        $signature=$identity.Rsa.SignData($payload,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pss)
        $result=@{signature=[Convert]::ToBase64String($signature)}
    } elseif ($inputData.action -in @('verify','verify_resources')) {
        Assert-Fields $inputData @('action','payload','signature','certificate','certificate_sha256')
        if ($inputData.action -eq 'verify_resources') {
            Assert-ResourcePayload $document 'prometheus.remote.resources.advertisement' $payload
        } else {
        if ($payload.Length -ge 16384) { throw 'Invalid receipt size.' }
        if ($document.purpose -ceq 'prometheus.remote.lint.receipt') { Assert-LintReceipt $document $payload }
        elseif ($document.purpose -cne 'prometheus.remote.syntax.receipt') { throw 'Invalid receipt purpose.' }
        }
        $der=[Convert]::FromBase64String($inputData.certificate)
        if ($der.Length -gt 16384 -or (Hash-Bytes $der) -cne $inputData.certificate_sha256) { throw 'Certificate pin mismatch.' }
        $certificate=[Security.Cryptography.X509Certificates.X509Certificate2]::new($der)
        $rsa=[Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
        $now=[DateTime]::UtcNow
        if (!$rsa -or $rsa.KeySize -lt 2048 -or $rsa.KeySize -gt 8192 -or
            $now -lt $certificate.NotBefore.ToUniversalTime() -or $now -ge $certificate.NotAfter.ToUniversalTime()) { throw 'Invalid certificate.' }
        $signature=[Convert]::FromBase64String($inputData.signature)
        $result=@{verified=$rsa.VerifyData($payload,$signature,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pss)}
    } else { throw 'Unknown crypto operation.' }
    [IO.File]::WriteAllText($OutputPath,($result | ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
} catch { exit 1 }
finally {
    if ($identity) { $identity.Rsa.Dispose(); $identity.Certificate.Dispose() }
    if ($rsa) { $rsa.Dispose() }
    if ($certificate) { $certificate.Dispose() }
}
