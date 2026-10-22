using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
[assembly: AssemblyVersion("3.0.0.0")]
[assembly: AssemblyFileVersion("3.0.0.0")]
// New source-built identity. Historical v2 binary provenance is not inferred.
internal static class PrometheusQuietHostV3 {
    internal static string RelativeScript(string mode) {
        switch (mode) {
            case "reconnect": return @"Prometheus\Prometheus-USB-Reconnect-Supervisor.ps1";
            case "recorder": return @"Prometheus\Diagnostics\Prometheus-Recovery-Recorder.ps1";
            case "watchdog": return @"Prometheus\Diagnostics\Prometheus-Recovery-Watchdog.ps1";
            case "ssd-backup": return @"Prometheus\ssd-memory-backup.ps1";
            case "backup": return @"Prometheus\host-memory-backup.ps1";
            case "volume-watcher": return @"Prometheus\Prometheus-Volume-Watcher.ps1";
            case "desktop-commander": return @"DesktopCommanderStartup\Run-DesktopCommander.ps1";
            default: return null;
        }
    }
    internal static bool PauseMarkerPresent(string path, Func<string,FileAttributes> attributes = null) {
        try { (attributes ?? File.GetAttributes)(path); return true; }
        catch (FileNotFoundException) { return false; }
        catch (DirectoryNotFoundException) { return false; }
        catch (IOException) { return true; }
        catch (UnauthorizedAccessException) { return true; }
        catch (System.Security.SecurityException) { return true; }
    }
    internal static bool ShouldSuppress(string local, string mode, Func<string,FileAttributes> attributes = null) {
        return mode != "reconnect" && mode != "recorder" &&
            (PauseMarkerPresent(Path.Combine(local, @"Prometheus\eject-mode.json"), attributes) ||
             PauseMarkerPresent(Path.Combine(local, @"DesktopCommanderStartup\runner-paused.request"), attributes));
    }
    internal static ProcessStartInfo ChildStart(string script) {
        var start = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe"));
        start.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\"";
        start.WorkingDirectory = Path.GetDirectoryName(script);
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.WindowStyle = ProcessWindowStyle.Hidden;
        start.RedirectStandardInput = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        return start;
    }
    internal static int RunChild(ProcessStartInfo start) {
        try {
            using (var process = new Process()) {
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
    // Test seam is internal to the assembly, never a CLI root/command override.
    internal static int Run(string[] args, string local) {
        if (args.Length == 1 && args[0] == "--probe") return 0;
        if (args.Length != 1) return 64;
        var relative = RelativeScript(args[0]);
        if (relative == null) return 64;
        if (ShouldSuppress(local, args[0])) return 0;
        var script = Path.Combine(local, relative);
        if (!File.Exists(script)) return 66;
        return RunChild(ChildStart(script));
    }
    [STAThread]
    static int Main(string[] args) {
        return Run(args, Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData));
    }
}
