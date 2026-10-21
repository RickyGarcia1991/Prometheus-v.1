using System;
using System.IO;
using System.Diagnostics;

// GUI-subsystem host: the child starts with CREATE_NO_WINDOW, before PowerShell
// can create a console. Only these existing background scripts are supported.
class PrometheusQuietHost {
    static bool ShouldSuppress(string local, string mode) {
        bool ejecting = File.Exists(Path.Combine(local, @"Prometheus\eject-mode.json"));
        bool paused = File.Exists(Path.Combine(local, @"DesktopCommanderStartup\runner-paused.request"));
        return mode != "reconnect" && mode != "recorder" && (ejecting || paused);
    }
    [STAThread]
    static int Main(string[] args) {
        if (args.Length == 1 && args[0] == "--probe") return 0;
        if (args.Length != 1) return 64;
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string relative;
        switch (args[0]) {
            case "reconnect": relative = @"Prometheus\Prometheus-USB-Reconnect-Supervisor.ps1"; break;
            case "recorder": relative = @"Prometheus\Diagnostics\Prometheus-Recovery-Recorder.ps1"; break;
            case "watchdog": relative = @"Prometheus\Diagnostics\Prometheus-Recovery-Watchdog.ps1"; break;
            case "ssd-backup": relative = @"Prometheus\ssd-memory-backup.ps1"; break;
            case "backup": relative = @"Prometheus\host-memory-backup.ps1"; break;
            case "volume-watcher": relative = @"Prometheus\Prometheus-Volume-Watcher.ps1"; break;
            case "desktop-commander": relative = @"DesktopCommanderStartup\Run-DesktopCommander.ps1"; break;
            default: return 64;
        }
        // Suppressed scheduled tasks exit before starting any PowerShell child.
        if (ShouldSuppress(local, args[0])) return 0;
        string script = Path.Combine(local, relative);
        if (!File.Exists(script)) return 66;
        try {
            ProcessStartInfo start = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"));
            start.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"";
            start.WorkingDirectory = Path.GetDirectoryName(script);
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            using (Process process = new Process()) {
                process.StartInfo = start;
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
                process.Start();
                process.StandardInput.Close();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                process.WaitForExit();
                return process.ExitCode;
            }
        } catch { return 70; }
    }
}
