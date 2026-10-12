param([ValidateSet('Host','SSD')][string]$Target = 'Host')
$ErrorActionPreference = 'Stop'

function Assert-BackupPlainPath([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked backup paths are refused.' }
        }
        $parent = [IO.Directory]::GetParent($current)
        if (!$parent) { break }
        $current = $parent.FullName
    }
}

function Get-PrometheusBackupPlan {
    param([ValidateSet('Host','SSD')][string]$Target, [object[]]$Volumes,
          [string]$LocalDataRoot, [string]$PackageRoot)
    if (!$LocalDataRoot -or ![IO.Path]::IsPathRooted($LocalDataRoot)) { throw 'A Windows user data directory is required.' }
    $found = @($Volumes | Where-Object { $_.VolumeName -eq 'Prometheus-2TB' -and $_.DriveType -in @(2,3) })
    if ($found.Count -ne 1) {
        return [pscustomobject]@{result='SKIP';reason='Exactly one Prometheus-2TB SSD must be connected.'}
    }
    if ([string]$found[0].DeviceID -notmatch '^[A-Za-z]:$') { throw 'The SSD drive letter is invalid.' }
    $root = [string]$found[0].DeviceID + '\'
    $localState = [IO.Path]::Combine($LocalDataRoot, 'Prometheus')
    $destination = if ($Target -eq 'Host') {
        [IO.Path]::Combine($localState, 'backups\memory-automatic')
    } else { [IO.Path]::Combine($root, 'Prometheus-Recovery\Memory-Backups') }
    return [pscustomobject]@{
        result='READY'; root=$root
        python=[IO.Path]::Combine($root, 'Prometheus-Resources\Python\python.exe')
        script=[IO.Path]::Combine($PackageRoot, 'scheduled-memory-backup.py')
        source=[IO.Path]::Combine($root, 'Prometheus-Data\memory.sqlite3')
        destination=$destination
        eject_marker=[IO.Path]::Combine($localState, 'eject-mode.json')
    }
}

function Invoke-PrometheusMemoryBackup([string]$Target) {
    $marker = [IO.Path]::Combine($env:LOCALAPPDATA, 'Prometheus\eject-mode.json')
    if (Test-Path -LiteralPath $marker) {
        Write-Output '{"result":"SKIP","reason":"SSD eject is in progress."}' | Out-Host
        return 0
    }
    # Resolve the currently attached volume each run; no saved drive letter,
    # developer checkout, active-release metadata or host Python is required.
    $volumes = @(Get-CimInstance Win32_LogicalDisk -ErrorAction Stop)
    $plan = Get-PrometheusBackupPlan -Target $Target -Volumes $volumes -LocalDataRoot $env:LOCALAPPDATA -PackageRoot $PSScriptRoot
    if ($plan.result -eq 'SKIP') { Write-Output ($plan | ConvertTo-Json -Compress) | Out-Host; return 0 }
    foreach ($path in @($plan.python, $plan.script, $plan.source, $plan.destination, $plan.eject_marker)) { Assert-BackupPlainPath $path }
    if (!(Test-Path -LiteralPath $plan.source -PathType Leaf)) {
        Write-Output '{"result":"SKIP","reason":"Source memory is absent; no database was created."}' | Out-Host
        return 0
    }
    if (!(Test-Path -LiteralPath $plan.python -PathType Leaf) -or !(Test-Path -LiteralPath $plan.script -PathType Leaf)) {
        throw 'Portable Python or the installed backup helper is missing.'
    }
    if (Test-Path -LiteralPath $plan.eject_marker) {
        Write-Output '{"result":"SKIP","reason":"SSD eject began; existing backups were preserved."}' | Out-Host
        return 0
    }
    & $plan.python -I -B -X utf8 $plan.script --source $plan.source --destination $plan.destination --keep 14 --eject-marker $plan.eject_marker | Out-Host
    return $LASTEXITCODE
}

if ($MyInvocation.InvocationName -ne '.') {
    try { exit (Invoke-PrometheusMemoryBackup -Target $Target) }
    catch { Write-Error 'Memory backup could not start. Existing memory and backups were preserved.' -ErrorAction Continue; exit 1 }
}
