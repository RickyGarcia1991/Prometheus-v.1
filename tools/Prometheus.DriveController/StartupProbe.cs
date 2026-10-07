using System.Diagnostics;
using System.IO;
namespace Prometheus.DriveController;
public sealed class StartupProbe
{
 public string? FindPrometheusDrive(){foreach(var di in DriveInfo.GetDrives()){try{if(di.IsReady&&di.VolumeLabel.Equals("Prometheus-2TB",StringComparison.OrdinalIgnoreCase))return di.RootDirectory.FullName;}catch{}}return null;}
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