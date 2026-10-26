using System;
using System.IO;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
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
    internal static string ResolveScript(string local, string mode, Func<string,FileAttributes> attributes = null) {
        var relative = RelativeScript(mode);
        if (relative == null) return null;
        var path = Path.Combine(local, relative);
        if (mode != "desktop-commander") return path;
        try { (attributes ?? File.GetAttributes)(path); return path; }
        catch (FileNotFoundException) { return Path.Combine(local, @"DesktopCommander\Start-AtSignIn.ps1"); }
        catch (DirectoryNotFoundException) { return Path.Combine(local, @"DesktopCommander\Start-AtSignIn.ps1"); }
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

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimit {
        internal long ProcessTime, JobTime;
        internal uint Flags;
        internal UIntPtr MinimumWorkingSet, MaximumWorkingSet;
        internal uint ActiveProcesses;
        internal UIntPtr Affinity;
        internal uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters { internal ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimit {
        internal BasicLimit Basic;
        internal IoCounters Io;
        internal UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    private struct StartupInfo {
        internal int Size;
        internal string Reserved, Desktop, Title;
        internal uint X, Y, Width, Height, XChars, YChars, Fill, Flags;
        internal short ShowWindow, ReservedSize;
        internal IntPtr ReservedBytes, Input, Output, Error;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct StartupInfoEx { internal StartupInfo Startup; internal IntPtr Attributes; }
    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInfo { internal IntPtr Process, Thread; internal uint Pid, Tid; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr CreateJobObject(IntPtr security, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimit limits, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool InitializeProcThreadAttributeList(IntPtr attributes, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UpdateProcThreadAttribute(IntPtr attributes, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr attributes);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CreateProcess(string application, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity,
        [MarshalAs(UnmanagedType.Bool)] bool inheritHandles, uint flags, IntPtr environment, string directory, ref StartupInfoEx startup, out ProcessInfo process);
    [DllImport("kernel32.dll")]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);

    // Reconnect owns only its immediate supervisor. Atomic job-list creation prevents
    // any child execution before containment, including forced parent death during startup.
    // Other modes retain intentional detached-agent behavior (notably Desktop Commander).
    internal static int RunContainedChild(ProcessStartInfo start) {
        IntPtr job=IntPtr.Zero, attributes=IntPtr.Zero, jobValue=IntPtr.Zero;
        bool initialized=false;
        ProcessInfo process=new ProcessInfo();
        try {
            job=CreateJobObject(IntPtr.Zero,null);
            if(job==IntPtr.Zero) return 70;
            var limit=new ExtendedLimit();
            limit.Basic.Flags=0x3000; // KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK.
            // Arrival/controller/application/communication children have independent lifetimes.
            // They escape this job; the explicitly assigned immediate supervisor does not.
            if(!SetInformationJobObject(job,9,ref limit,(uint)Marshal.SizeOf(typeof(ExtendedLimit)))) return 70;
            IntPtr size=IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
            if(size==IntPtr.Zero) return 70;
            attributes=Marshal.AllocHGlobal(size);
            if(!InitializeProcThreadAttributeList(attributes,1,0,ref size)) return 70;
            initialized=true;
            jobValue=Marshal.AllocHGlobal(IntPtr.Size);
            Marshal.WriteIntPtr(jobValue,job);
            // PROC_THREAD_ATTRIBUTE_JOB_LIST: Windows assigns the job during CreateProcess.
            if(!UpdateProcThreadAttribute(attributes,0,new IntPtr(0x2000D),jobValue,new IntPtr(IntPtr.Size),IntPtr.Zero,IntPtr.Zero)) return 70;
            var startup=new StartupInfoEx();
            startup.Startup.Size=Marshal.SizeOf(typeof(StartupInfoEx));
            startup.Startup.Flags=1; // STARTF_USESHOWWINDOW
            startup.Startup.ShowWindow=0;
            startup.Attributes=attributes;
            // No console and no inherited handles: the child cannot retain the job handle.
            // Standard streams are deliberately not inherited; output cannot fill parent pipes.
            if(!CreateProcess(start.FileName,new StringBuilder("\""+start.FileName+"\" "+start.Arguments),IntPtr.Zero,IntPtr.Zero,
                false,0x08080000,IntPtr.Zero,start.WorkingDirectory,ref startup,out process)) return 70;
            if(WaitForSingleObject(process.Process,0xFFFFFFFF)!=0) return 70;
            uint code;
            return GetExitCodeProcess(process.Process,out code) ? unchecked((int)code) : 70;
        } catch { return 70; }
        finally {
            // Closing the sole job handle terminates only the owned supervisor. Its launched
            // applications/communication processes silently break away and remain independent.
            if(job!=IntPtr.Zero) CloseHandle(job);
            if(process.Thread!=IntPtr.Zero) CloseHandle(process.Thread);
            if(process.Process!=IntPtr.Zero) CloseHandle(process.Process);
            if(initialized) DeleteProcThreadAttributeList(attributes);
            if(attributes!=IntPtr.Zero) Marshal.FreeHGlobal(attributes);
            if(jobValue!=IntPtr.Zero) Marshal.FreeHGlobal(jobValue);
        }
    }
    // Test seam is internal to the assembly, never a CLI root/command override.
    internal static int Run(string[] args, string local) {
        if (args.Length == 1 && args[0] == "--probe") return 0;
        if (args.Length != 1) return 64;
        var relative = RelativeScript(args[0]);
        if (relative == null) return 64;
        if (ShouldSuppress(local, args[0])) return 0;
        string script;
        try { script = ResolveScript(local, args[0]); } catch { return 70; }
        if (!File.Exists(script)) return 66;
        return args[0] == "reconnect" ? RunContainedChild(ChildStart(script)) : RunChild(ChildStart(script));
    }
    [STAThread]
    static int Main(string[] args) {
        return Run(args, Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData));
    }
}
