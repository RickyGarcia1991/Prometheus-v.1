using System.Diagnostics;
using System.IO;
namespace Prometheus.DriveController;
public sealed class StartupProbe
{
 public string? FindPrometheusDrive(){foreach(var di in DriveInfo.GetDrives()){try{if(di.IsReady&&di.VolumeLabel.Equals("Prometheus-2TB",StringComparison.OrdinalIgnoreCase))return di.RootDirectory.FullName;}catch{}}return null;}
 public (bool Healthy,string Message) DriveHealth(string root){try{var di=new DriveInfo(root);if(!di.IsReady)return(false,"SSD detected but Windows reports it is not ready.");if(!di.VolumeLabel.Equals("Prometheus-2TB",StringComparison.OrdinalIgnoreCase))return(false,"Unexpected volume identity.");if(!di.DriveFormat.Equals("NTFS",StringComparison.OrdinalIgnoreCase))return(false,$"Unexpected filesystem: {di.DriveFormat}.");var required=new[]{Path.Combine(root,"PROMETHEUS-SSD-STATUS.json"),Path.Combine(root,"START-PROMETHEUS-SSD.cmd"),Path.Combine(root,"Prometheus-Resources")};if(required.Any(x=>!File.Exists(x)&&!Directory.Exists(x)))return(false,"SSD is readable but required Prometheus files are missing.");using(var fs=new FileStream(Path.Combine(root,"PROMETHEUS-SSD-STATUS.json"),FileMode.Open,FileAccess.Read,FileShare.ReadWrite)){if(fs.Length==0)return(false,"SSD status file is empty.");}return(true,"Healthy • Readable • NTFS");}catch(IOException ex){return(false,"SSD read error: "+ex.Message);}catch(UnauthorizedAccessException){return(false,"SSD access denied by Windows.");}catch(Exception ex){return(false,"SSD health check failed: "+ex.Message);}}
 public string? FindActiveRelease(string root){try{return Directory.EnumerateDirectories(root,"Prometheus-v*").Where(x=>File.Exists(Path.Combine(x,"prometheus.py"))).OrderByDescending(x=>Directory.GetLastWriteTimeUtc(x)).FirstOrDefault();}catch{return null;}}
 public Task<HashSet<string>> MeasureAsync(string? drive=null)
 {
  var root=drive??FindPrometheusDrive()??"D:\\"; var done=new HashSet<string>();
  try{var di=new DriveInfo(root);if(di.IsReady&&di.VolumeLabel=="Prometheus-2TB")done.Add("Verify Prometheus SSD identity");}catch{}
  if(File.Exists(Path.Combine(root,"Prometheus-Resources","Python","python.exe"))&&File.Exists(Path.Combine(root,"START-PROMETHEUS-SSD.cmd")))done.Add("Validate portable runtime");
  if(Process.GetProcessesByName("ollama").Any(p=>SafePath(p).StartsWith(root,StringComparison.OrdinalIgnoreCase)))done.Add("Start Ollama service");
  if(Process.GetProcessesByName("llama-server").Any(p=>SafePath(p).StartsWith(root,StringComparison.OrdinalIgnoreCase)))done.Add("Load configured model");
  var mem=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Prometheus","memory.sqlite3");if(File.Exists(mem)&&new FileInfo(mem).Length>0)done.Add("Verify local memory database");
  var models=Path.Combine(root,"Prometheus-Resources","Ollama",".ollama","models","blobs");if(Directory.Exists(models)&&Directory.EnumerateFiles(models).Any())done.Add("Verify portable model store");
  if(FindActiveRelease(root)!=null)done.Add("Verify Prometheus source tree");
  if(Process.GetProcessesByName("python").Any(p=>SafePath(p).StartsWith(root,StringComparison.OrdinalIgnoreCase)))done.Add("Open chat runtime");
  return Task.FromResult(done);
 }
 private static string SafePath(Process p){try{return p.MainModule?.FileName??"";}catch{return "";}}
}