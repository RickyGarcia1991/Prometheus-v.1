using System.Diagnostics;
using System.IO;
using System.Text.Json;
namespace Prometheus.DriveController;
public sealed class LegacyControllerBridge
{
    private readonly string _script=FindController();
    public string ControllerPath=>_script;
    private static string FindController()
    {
        var home=Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var candidates=new[]{
            Path.Combine(AppContext.BaseDirectory,"Prometheus-Drive-Engine.ps1"),
            Path.Combine(home,"Documents","Removable-Media-Status","Prometheus-Drive-Engine.ps1"),
            Path.Combine(home,"OneDrive","Documents","Removable-Media-Status","Prometheus-Drive-Engine.ps1"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),"Removable-Media-Status","Prometheus-Drive-Engine.ps1")};
        return candidates.FirstOrDefault(File.Exists) ?? candidates[0];
    }
    public async Task<BridgeResult> RunAsync(string action,string drive="D:")
    {
        if(!File.Exists(_script)) return new(2,null,"",$"Controller not found: {_script}");
        var psi=new ProcessStartInfo("powershell.exe") { UseShellExecute=false, RedirectStandardOutput=true, RedirectStandardError=true, CreateNoWindow=true };
        foreach(var a in new[]{"-NoProfile","-ExecutionPolicy","Bypass","-File",_script,"-Drive",drive,"-Action",action}) psi.ArgumentList.Add(a);
        using var p=Process.Start(psi) ?? throw new InvalidOperationException("Could not start controller bridge.");
        var stdoutTask=p.StandardOutput.ReadToEndAsync(); var stderrTask=p.StandardError.ReadToEndAsync();
        await p.WaitForExitAsync(); var stdout=await stdoutTask; var stderr=await stderrTask;
        BridgeStatus? status=null; try { status=JsonSerializer.Deserialize<BridgeStatus>(stdout,new JsonSerializerOptions{PropertyNameCaseInsensitive=true}); } catch { }
        return new(p.ExitCode,status,stdout,stderr);
    }
}
public sealed record BridgeResult(int ExitCode,BridgeStatus? Status,string StdOut,string StdErr);
public sealed record BridgeStatus(string? Phase,string? Message,string? Activity,string[]? Checks,string[]? Errors,string? Updated);
