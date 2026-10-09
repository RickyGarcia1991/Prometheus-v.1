param([string]$RuntimeRoot, [switch]$CheckOnly, [switch]$NoBrowser, [switch]$NoDialog)
$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$studioLog = Join-Path $studioRoot 'startup-error.txt'
try {
if(Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Prometheus\eject-mode.json')){throw 'SSD eject is active. Reconnect the drive or explicitly Start Prometheus first.'}
# Only the sibling runtime of this installation is selected automatically.
# A development caller may explicitly supply a trusted runtime directory.
if (-not $RuntimeRoot) { $RuntimeRoot = Join-Path (Split-Path -Parent $studioRoot) 'Prometheus-Resources\Python' }
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$runtimePins = Get-Content -LiteralPath (Join-Path $studioRoot 'runtime-pins.json') -Raw | ConvertFrom-Json
function Get-StudioHash([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','') }
    finally { $stream.Dispose(); $sha.Dispose() }
}
foreach ($pin in $runtimePins) {
    if ($pin.file -notmatch '^[A-Za-z0-9_.-]+$') { throw 'Invalid runtime integrity entry.' }
    $runtimeFile = Join-Path $RuntimeRoot $pin.file
    if (-not (Test-Path -LiteralPath $runtimeFile -PathType Leaf)) { throw "The intended Prometheus runtime is missing: $runtimeFile" }
    if ((Get-StudioHash $runtimeFile) -ne $pin.sha256) { throw "Runtime integrity check failed: $($pin.file)" }
}
$studioPython = Join-Path $RuntimeRoot 'pythonw.exe'
if ($CheckOnly) { Write-Output "Verified runtime: $studioPython"; exit 0 }
# Isolated interpreter: ignore PYTHONPATH, user packages and automatic site hooks.
$readyFile = Join-Path $studioRoot ('.ready-' + [guid]::NewGuid().ToString('N') + '.txt')
$process = Start-Process -FilePath $studioPython -ArgumentList @('-I','-S','-B',('"' + (Join-Path $studioRoot 'studio.py') + '"'),'--no-browser','--ready-file',('"' + $readyFile + '"')) -WorkingDirectory $studioRoot -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $studioRoot 'startup-stderr.txt') -RedirectStandardOutput (Join-Path $studioRoot 'startup-stdout.txt')
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while (-not (Test-Path -LiteralPath $readyFile)) {
    if ($process.HasExited) { throw 'The local server stopped during startup. See startup-stderr.txt in this folder.' }
    if ([DateTime]::UtcNow -ge $deadline) { throw 'Startup exceeded 20 seconds. See startup-stderr.txt in this folder.' }
    Start-Sleep -Milliseconds 200
}
$url = (Get-Content -LiteralPath $readyFile -Raw).Trim()
Remove-Item -LiteralPath $readyFile
if ($url -notmatch '^http://127\.0\.0\.1:[0-9]{1,5}$') { throw 'The server returned an invalid local address.' }
[System.IO.File]::WriteAllText((Join-Path $studioRoot 'OPEN-STUDIO.txt'),"Prometheus Maps & Studio`r`nOpen this address while the launcher is running:`r`n$url/`r`n")
if ($NoBrowser) { Write-Output "$url/" }
else {
    try { Start-Process -FilePath ($url + '/') }
    catch { throw "The workspace is running at $url/ but the browser did not open. Paste that address into your browser." }
}
} catch {
    $failure = $_.Exception.Message
    try { [System.IO.File]::WriteAllText($studioLog,([DateTime]::UtcNow.ToString('o') + "`r`n" + $failure)) } catch { }
    if (-not $CheckOnly -and -not $NoDialog) {
        try { $shell = New-Object -ComObject WScript.Shell; [void]$shell.Popup(($failure + "`r`n`r`nDetails: " + $studioLog),0,'Prometheus Maps & Studio could not open',16) } catch { }
    }
    Write-Error $failure
    exit 1
}
