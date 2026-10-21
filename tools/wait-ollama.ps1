param([ValidateRange(1,600)][int]$TimeoutSeconds=120)

function Test-PrometheusRuntimeReady {
    try {
        $result=Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
        return $null -ne $result.models
    } catch { return $false }
}

function Get-PrometheusRuntimeProcessKind {
    param($Process, [string]$ExpectedPath)
    if ([string]::IsNullOrWhiteSpace([string]$Process.ExecutablePath)) { return 'unknown' }
    if (![string]::Equals([string]$Process.ExecutablePath, $ExpectedPath,
                         [StringComparison]::OrdinalIgnoreCase)) { return 'other' }
    $command=[string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($command)) { return 'unknown' }
    $match=[regex]::Match($command, '^\s*(?:"[^"]+"|\S+)\s+(?<verb>\S+)(?:\s|$)')
    if (!$match.Success) { return 'unknown' }
    $verb=$match.Groups['verb'].Value
    if ($verb -ceq 'serve') { return 'server' }
    if ($verb -ceq 'runner') { return 'runner' }
    return 'unknown'
}

function Get-PrometheusRuntimeProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'ollama.exe'" -OperationTimeoutSec 5
}

function Get-PrometheusRuntimeTick {
    return [Diagnostics.Stopwatch]::GetTimestamp() / [double][Diagnostics.Stopwatch]::Frequency
}

function Wait-PrometheusRuntimePoll { Start-Sleep -Milliseconds 500 }

function Start-PrometheusRuntimeProcess {
    param([string]$ExpectedPath)
    $logs=Join-Path $env:LOCALAPPDATA 'Prometheus\logs'
    New-Item -ItemType Directory -Force $logs | Out-Null
    $stamp=(Get-Date -Format yyyyMMdd-HHmmss) + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
    $stderr=Join-Path $logs ('ollama-'+$stamp+'.stderr.log')
    $stdout=Join-Path $logs ('ollama-'+$stamp+'.stdout.log')
    $process=Start-Process $ExpectedPath -ArgumentList serve -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    return [pscustomobject]@{ Process=$process; ErrorLog=$stderr }
}

function Get-PrometheusRuntimePhase {
    param([string]$Path)
    # Bound reads and return only known labels, never arbitrary log text.
    if ([string]::IsNullOrWhiteSpace($Path)) { return 'unknown' }
    $stream=$null
    try {
        $stream=[IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                              [IO.FileShare]::ReadWrite)
        $count=[int][Math]::Min(16384, $stream.Length)
        [void]$stream.Seek(-$count, [IO.SeekOrigin]::End)
        $bytes=New-Object byte[] $count
        $read=$stream.Read($bytes, 0, $count)
        $text=[Text.Encoding]::UTF8.GetString($bytes, 0, $read)
        $phase='unknown'
        foreach ($line in ($text -split "`n")) {
            if ($line -match 'msg="Listening on ') { $phase='listening; API not ready' }
            if ($line -match 'msg="discovering available GPUs\.\.\."') { $phase='GPU discovery' }
            if ($line -match 'msg="inference compute"') { $phase='compute discovery complete; API not ready' }
        }
        return $phase
    } catch { return 'unknown' }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
}

function Invoke-PrometheusRuntimeStartup {
    param([ValidateRange(1,600)][int]$TimeoutSeconds=120)
    $ErrorActionPreference='Stop'
    if (Test-PrometheusRuntimeReady) {
        return [pscustomobject]@{ Success=$true; Reason='already_ready'; Ownership='existing'; Phase='API ready'; ProcessId=$null; LogPath=$null }
    }
    if (!(Test-Path -LiteralPath $env:OLLAMA_EXE -PathType Leaf)) { throw 'Portable Ollama executable missing.' }
    $expected=[IO.Path]::GetFullPath($env:OLLAMA_EXE)
    $existing=@(Get-PrometheusRuntimeProcesses | Where-Object {
        (Get-PrometheusRuntimeProcessKind $_ $expected) -in @('server','unknown')
    })
    $owned=$null
    $errorLog=$null
    $processId=$null
    $ownership='existing_server'
    if ($existing.Count) {
        if (@($existing | Where-Object { (Get-PrometheusRuntimeProcessKind $_ $expected) -eq 'unknown' }).Count) {
            $ownership='existing_ambiguous_process'
        }
    } else {
        if (Test-PrometheusRuntimeReady) {
            return [pscustomobject]@{ Success=$true; Reason='already_ready'; Ownership='existing'; Phase='API ready'; ProcessId=$null; LogPath=$null }
        }
        $started=Start-PrometheusRuntimeProcess $expected
        $owned=$started.Process
        $errorLog=$started.ErrorLog
        $processId=$owned.Id
        $ownership='owned'
    }
    $deadline=(Get-PrometheusRuntimeTick)+$TimeoutSeconds
    while ((Get-PrometheusRuntimeTick) -lt $deadline) {
        if (Test-PrometheusRuntimeReady) {
            return [pscustomobject]@{ Success=$true; Reason='ready'; Ownership=$ownership; Phase='API ready'; ProcessId=$processId; LogPath=$errorLog }
        }
        if ($null -ne $owned) {
            $owned.Refresh()
            if ($owned.HasExited) {
                return [pscustomobject]@{ Success=$false; Reason='owned_process_exited'; Ownership=$ownership;
                    Phase=(Get-PrometheusRuntimePhase $errorLog); ProcessId=$processId; LogPath=$errorLog }
            }
        }
        Wait-PrometheusRuntimePoll
    }
    $reason=if ($ownership -eq 'owned') { 'readiness_timeout' } elseif ($ownership -eq 'existing_server') {
        'existing_server_unready'
    } else { 'existing_process_identity_uncertain' }
    return [pscustomobject]@{ Success=$false; Reason=$reason; Ownership=$ownership;
        Phase=(Get-PrometheusRuntimePhase $errorLog); ProcessId=$processId; LogPath=$errorLog }
}

# Dot-sourcing exposes functions and never launches a process.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $outcome=Invoke-PrometheusRuntimeStartup -TimeoutSeconds $TimeoutSeconds
        if ($outcome.Success) { Write-Output 'Local Ollama API readiness verified.'; exit 0 }
        Write-Error -ErrorAction Continue ('Local Ollama startup: '+$outcome.Reason+'. Phase: '+$outcome.Phase+
            '. Runtime processes were preserved.'+$(if($outcome.LogPath){' Log: '+$outcome.LogPath}else{''}))
        exit 1
    } catch {
        Write-Error -ErrorAction Continue 'Local Ollama startup could not be completed. Verify the portable executable and process access. Runtime processes were preserved.'
        exit 1
    }
}
