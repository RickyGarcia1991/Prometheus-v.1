from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"tools"/"Prometheus.DriveController"
TOOLS=ROOT.parent

def test_watcher_stops_polling_mounted_ssd_after_controller_is_cached():
    text=(ROOT/"Prometheus-Drive-Watcher.ps1").read_text(encoding="utf-8")
    guard="if($seen.ContainsKey($d.DeviceID))"
    assert guard in text and "if($controller.Count){continue}" in text
    assert text.index(guard) < text.index("$status=Join-Path $root 'PROMETHEUS-SSD-STATUS.json'")

def test_eject_lock_blocks_watcher_and_recovers_after_reinsert_grace():
    text=(ROOT/"Prometheus-Drive-Watcher.ps1").read_text(encoding="utf-8")
    assert "eject-mode.json" in text
    assert "if($age -lt 15){Start-Sleep 3;continue}" in text
    assert "Remove-Item $ejectLock" in text

def test_controller_pauses_refresh_and_starts_final_handoff():
    text=(ROOT/"MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert 'if(s.Phase=="green")_timer.Stop();' in text
    assert "Prometheus-Eject-Handoff.ps1" in text
    assert "Final handoff started." in text
    assert "_timer.Start();_operationActive=true" in text

def test_exact_device_vetoes_are_not_filtered_by_process_location():
    text=(ROOT/"Prometheus-Drive-Engine.ps1").read_text(encoding="utf-8")
    assert "$e=ExactBlockers;$live=@($e)" in text
    assert "merely because the blocking process itself runs from the internal drive" in text

def test_stop_sets_eject_lock_and_start_clears_it():
    text=(ROOT/"Prometheus-Drive-Engine.ps1").read_text(encoding="utf-8")
    assert 'phase="stopping"' in text
    start=text.index('if($Action -eq "start")')
    stop=text.index('if($Action -eq "stop")')
    assert "Remove-Item $lock" in text[start:stop]

def test_handoff_logs_locally_and_disconnects_desktop_commander_last():
    text=(ROOT/"Prometheus-Eject-Handoff.ps1").read_text(encoding="utf-8")
    assert "Diagnostics" in text and "eject-history.jsonl" in text
    assert "DesktopCommanderStartup|desktop-commander" in text
    assert "Prometheus-Drive-Watcher" in text
    assert "ExecutablePath.StartsWith($drivePrefix" in text
    assert "runner-paused.request" in text
    assert "SSD-backed processes remain; refusing remote disconnect." in text

def test_windows_side_supervisor_restores_commander_after_volume_disappears():
    text=(ROOT/"Prometheus-USB-Reconnect-Supervisor.ps1").read_text(encoding="utf-8")
    assert "runner-paused.request" in text
    assert "Run-DesktopCommander.ps1" in text
    assert "if(!$present)" in text
    assert "Desktop Commander restart requested" in text

def test_flash_drive_preparer_excludes_large_resources_by_default():
    text=(TOOLS/"PREPARE-FLASH-DRIVE.ps1").read_text(encoding="utf-8")
    assert "resources_included=$false" in text
    assert "Large Ollama/Kiwix resources are intentionally not copied" in text
