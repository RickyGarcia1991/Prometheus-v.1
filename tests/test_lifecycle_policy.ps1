param([string]$Root=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
. (Join-Path $Root 'tools\Prometheus.DriveController\Prometheus-Lifecycle.ps1')
. (Join-Path $Root 'tools\Prometheus.DriveController\Prometheus-Controller-Common.ps1')
$count=0
function Assert($actual,$expected,[string]$why){if($actual -ne $expected){throw "$why expected=$expected actual=$actual"};$script:count++}
$state=[pscustomobject]@{id='fixture';drive='Z:';usb_instance='USB\VID_1234&PID_ABCD\TEST';phase='released'}
Assert (Get-EjectDecision $null $true $true) 'normal' 'No eject request'
Assert (Get-EjectDecision $state $true $false) 'hold' 'Logical removal does not prove unplug'
Assert (Get-EjectDecision $state $true $true) 'hold' 'Still mounted'
Assert (Get-EjectDecision $state $null $false) 'hold' 'Unknown presence fails closed'
Assert (Get-EjectDecision $state $false $true) 'hold' 'Contradictory mounted volume fails closed'
Assert (Get-EjectDecision $state $false $false) 'disconnected' 'Actual absence allows resume'
$state.phase='blocked'
Assert (Get-EjectDecision $state $true $false) 'hold' 'Veto retains the pause'
$script:device=[pscustomobject]@{Present=$true;ConfigManagerErrorCode=0}
function Get-CimInstance {param($ClassName,$Filter,$ErrorAction) if($null -ne $script:device){$script:device}}
Assert (Get-PhysicalUsbPresence $state.usb_instance) $true 'Connected USB device'
$script:device=[pscustomobject]@{Present=$false;ConfigManagerErrorCode=47}
Assert (Get-PhysicalUsbPresence $state.usb_instance) $true 'Windows code 47 is prepared but attached'
$script:device=[pscustomobject]@{Present=$false;ConfigManagerErrorCode=45}
Assert (Get-PhysicalUsbPresence $state.usb_instance) $false 'Disconnected device'
$script:device=$null
Assert (Get-PhysicalUsbPresence $state.usb_instance) $false 'Removed device record'
$failed=$false;try{Get-PhysicalUsbPresence 'Z:'}catch{$failed=$true};Assert $failed $true 'Drive letter cannot substitute for USB identity'
$script:device=[pscustomobject]@{Present=$null;ConfigManagerErrorCode=0}
$failed=$false;try{Get-PhysicalUsbPresence $state.usb_instance}catch{$failed=$true};Assert $failed $true 'Missing presence is an error'
$oldLocal=$env:LOCALAPPDATA
$fixture=Join-Path $Root ('.pytest_cache\Prometheus-lifecycle-'+[guid]::NewGuid().ToString('N'))
try {
 $env:LOCALAPPDATA=$fixture
 $lock=Join-Path $fixture 'Prometheus\eject-mode.json'
 $pause=Join-Path $fixture 'DesktopCommanderStartup\runner-paused.request'
 [IO.Directory]::CreateDirectory((Split-Path $pause))|Out-Null
 Write-LifecycleJson $lock $state
 Assert (Get-EjectState).id 'fixture' 'Atomic state round trip'
 Set-Content -LiteralPath $pause -Value 'Another application owns this pause'
 $wrong=[pscustomobject]@{id='wrong'}
 $failed=$false;try{Resume-Lifecycle $wrong 'fixture'}catch{$failed=$true};Assert $failed $true 'Wrong owner cannot resume'
 Assert (Test-Path -LiteralPath $lock) $true 'Rejected resume preserves state'
 Resume-Lifecycle $state 'test-physical-disconnect'
 Assert (Get-Content -LiteralPath $pause -Raw).Trim() 'Another application owns this pause' 'Foreign pause preserved'
 Assert (Test-Path -LiteralPath $lock) $false 'Owned latch released'
 Write-LifecycleJson $lock $state
 Set-Content -LiteralPath $pause -Value 'Prometheus eject fixture'
 Resume-Lifecycle $state 'explicit-user-start'
 Assert (Test-Path -LiteralPath $pause) $false 'Only own pause released'
}finally{$env:LOCALAPPDATA=$oldLocal}
$studio=[pscustomobject]@{Name='pythonw.exe';ExecutablePath='Z:\Prometheus-Resources\Python\pythonw.exe';CommandLine='pythonw.exe "Z:\Prometheus-Maps-Studio\studio.py" --no-browser'}
Assert (Test-PrometheusStudio $studio 'Z:') $true 'Owned Studio joins shutdown'
$studio.ExecutablePath='C:\Other\pythonw.exe'
Assert (Test-PrometheusStudio $studio 'Z:') $false 'Unrelated Python preserved'
$health=[pscustomobject]@{localExecutorPid=42}
$node='C:\Fixture\node.exe'
$child=[pscustomobject]@{Name='conhost.exe';ExecutablePath=(Join-Path $env:SystemRoot 'System32\conhost.exe');ProcessId=43}
Assert (Get-DesktopCommanderChildRole $child $health $node) 'console-host' 'Owned Windows console host is recognized'
$child.ExecutablePath='C:\Other\conhost.exe'
Assert (Get-DesktopCommanderChildRole $child $health $node) 'unknown' 'Imitation console host is rejected'
$child=[pscustomobject]@{Name='node.exe';ExecutablePath=$node;ProcessId=42}
Assert (Get-DesktopCommanderChildRole $child $health $node) 'executor' 'Exact reported executor is recognized'
$child.ProcessId=44
Assert (Get-DesktopCommanderChildRole $child $health $node) 'unknown' 'Other node work is rejected'
$child.ProcessId=42;$child.ExecutablePath='C:\Other\node.exe'
Assert (Get-DesktopCommanderChildRole $child $health $node) 'unknown' 'Executor runtime identity must match'
@{passed=$count;failed=0;scope='Isolated lifecycle decisions, persisted ownership and process classification; no live device actions'}|ConvertTo-Json -Compress
