from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"tools"/"Prometheus.DriveController"

def test_drive_health_requires_identity_ntfs_and_core_files():
    text=(ROOT/"StartupProbe.cs").read_text(encoding="utf-8")
    assert 'DriveHealth(string root)' in text
    assert 'DriveFormat.Equals("NTFS"' in text
    assert '"PROMETHEUS-SSD-STATUS.json"' in text
    assert '"START-PROMETHEUS-SSD.cmd"' in text
    assert '"Prometheus-Resources"' in text

def test_drive_health_ui_is_green_only_when_healthy():
    text=(ROOT/"MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert 'DriveStateText.Text=health.Healthy?"● HEALTHY / READY":"● ERROR"' in text
    assert 'DriveStateText.Foreground=health.Healthy?Green:Red' in text
    assert 'STATUS: SSD ERROR' in text
    assert 'Prometheus will not write to the drive until it is readable.' in text
