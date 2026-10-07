from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"tools"/"Prometheus.DriveController"

def test_watcher_stops_polling_mounted_ssd_after_controller_is_cached():
    text=(ROOT/"Prometheus-Drive-Watcher.ps1").read_text(encoding="utf-8")
    guard="if($seen.ContainsKey($d.DeviceID))"
    assert guard in text
    assert "if($controller.Count){continue}" in text
    assert text.index(guard) < text.index("$status=Join-Path $root 'PROMETHEUS-SSD-STATUS.json'")

def test_controller_pauses_refresh_after_verified_shutdown():
    text=(ROOT/"MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert '_timer.Stop();Log("Shutdown verified; SSD polling paused.' in text
    assert "_timer.Start();_operationActive=true" in text

def test_exact_device_vetoes_are_not_filtered_by_process_location():
    text=(ROOT/"Prometheus-Drive-Engine.ps1").read_text(encoding="utf-8")
    assert "$e=ExactBlockers;$live=@($e)" in text
    assert "merely because the blocking process itself runs from the internal drive" in text
