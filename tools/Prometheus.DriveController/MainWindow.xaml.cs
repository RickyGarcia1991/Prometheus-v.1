using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Media;
namespace Prometheus.DriveController;
public partial class MainWindow:Window{
 readonly LegacyControllerBridge _bridge=new(); readonly StartupProbe _probe=new();
 static readonly Brush Green=new SolidColorBrush(Color.FromRgb(25,195,125)), Yellow=new SolidColorBrush(Color.FromRgb(228,183,44)), Red=new SolidColorBrush(Color.FromRgb(239,68,68)), Muted=new SolidColorBrush(Color.FromRgb(129,153,170));
 readonly System.Windows.Threading.DispatcherTimer _timer=new(){Interval=TimeSpan.FromSeconds(5)};
 bool _operationActive,_refreshing,_ejecting,_desiredRunning=File.Exists(DesiredStatePath),_repairing; int _repairAttempts; DateTime _nextRepair=DateTime.MinValue; const int MaxRepairAttempts=3; static string DesiredStatePath=>Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Prometheus","desired-running.flag"); void SetDesired(bool value){_desiredRunning=value;Directory.CreateDirectory(Path.GetDirectoryName(DesiredStatePath)!);if(value)File.WriteAllText(DesiredStatePath,DateTime.Now.ToString("o"));else if(File.Exists(DesiredStatePath))File.Delete(DesiredStatePath);}
 public MainWindow(){InitializeComponent();ShowPlan(ProgressPlan.Startup);_=RefreshAsync();_timer.Tick+=async(_,_)=>{if(_operationActive||_refreshing||_ejecting)return;_refreshing=true;try{await RefreshAsync();}catch(Exception ex){Visual("red");EjectText.Text="No";Log(ex.Message);}finally{_refreshing=false;}};_timer.Start();var args=Environment.GetCommandLineArgs();if(args.Length==3&&args[1]=="--verify-layout")Loaded+=async(_,_)=>await VerifyLayoutAsync(args[2]);}
 void Visual(string phase){var b=phase=="green"?Green:phase=="red"?Red:Yellow;StatusBorder.BorderBrush=b;StateText.Foreground=b;EjectText.Foreground=b;}
 void Log(string s){LogText.AppendText($"[{DateTime.Now:HH:mm:ss}] {s}{Environment.NewLine}");LogText.ScrollToEnd();}
 IEnumerable<ProgressStep> _plan=ProgressPlan.Startup;
 ISet<string> _lastCompleted=new HashSet<string>();
 bool _startupFailed;
 public sealed record StepRow(string Label,string WeightText,string State,string Icon,Brush Color,int Value,string Detail);
 void ShowPlan(IEnumerable<ProgressStep> steps,ISet<string>? completed=null,bool ready=false){
 _plan=steps;_lastCompleted=completed??new HashSet<string>();
 var a=steps.Select(x=>x with{Complete=_lastCompleted.Contains(x.Name)}).ToArray();
 StepsList.ItemsSource=a.Select(x=>{
 bool error=!x.Complete&&(_startupFailed||(!_operationActive&&x.Name.StartsWith("Verify"))||(!_operationActive&&x.Name.StartsWith("Validate")));
 var state=x.Complete?"Passed":error?"Issue":_operationActive?"Checking":"Stopped";
 return new StepRow(Short(x.Name),$"{x.Weight}%",state,x.Complete?"●":error?"●":"●",x.Complete?Green:error?Red:Yellow,x.Complete?100:0,x.Complete?"Check passed":error?"Check did not pass. See live log.":_operationActive?"Waiting for measured confirmation":"Service stopped or check pending");
 }).ToArray();
 var pct=ProgressPlan.Percent(a);OverallProgress.Value=ready?100:pct;PercentText.Text=ready?"Ready":$"{pct}%";
 }
 static string DriveSummary(string root){try{var di=new DriveInfo(root);double tb=di.TotalSize/1099511627776d,free=di.AvailableFreeSpace/1099511627776d;return $"{tb:F2} TB total • {free:F2} TB free • Seagate • {di.DriveFormat} • {root}";}catch{return $"Seagate • NTFS • {root}";}}
 static string Short(string s)=>s.Replace("Verify Prometheus SSD identity","SSD identity").Replace("Validate portable runtime","Portable runtime").Replace("Start Ollama service","Ollama service").Replace("Load configured model","Model load").Replace("Verify local memory database","Memory database").Replace("Verify portable model store","Model store").Replace("Verify Prometheus source tree","Source tree").Replace("Open chat runtime","Chat runtime");
 async Task RefreshAsync(){var root=_probe.FindPrometheusDrive();DriveInfoText.Text=root==null?"Not detected":DriveSummary(root);DeviceText.Text=$"SSD: {(root??"Not detected")}";if(root==null){DriveStateText.Text="Missing / unreadable";DriveStateText.Foreground=Red;Visual("red");StateText.Text="STATUS: SSD MISSING";ActivityText.Text="Prometheus SSD is not mounted as a readable volume.";EjectText.Text="No";StartButton.IsEnabled=false;StopButton.IsEnabled=false;return;}var health=_probe.DriveHealth(root);DriveStateText.Text=health.Healthy?"● HEALTHY / READY":"● ERROR";DriveStateText.Foreground=health.Healthy?Green:Red;if(!health.Healthy){Visual("red");StateText.Text="STATUS: SSD ERROR";ActivityText.Text=health.Message+" Windows disk repair may be required; Prometheus will not write to the drive until it is readable.";OperationText.Text="Drive health";EjectText.Text="No";StartButton.IsEnabled=false;StopButton.IsEnabled=false;Log("SSD health: "+health.Message);return;}var done=await _probe.MeasureAsync(root);UpdateComponents(done);ShowPlan(ProgressPlan.Startup,done);if(_desiredRunning){await WatchdogAsync(root,done);if(_repairing)return;}ApplyStatus(await _bridge.RunAsync("status",root.TrimEnd('\\')));}
 async Task WatchdogAsync(string root,ISet<string> done){
  var missing=new[]{"Start Ollama service","Load configured model","Open chat runtime"}.Where(x=>!done.Contains(x)).ToArray();
  if(missing.Length==0){_repairAttempts=0;_nextRepair=DateTime.MinValue;RepairText.Text="WATCHDOG: HEALTHY / ALL RUNTIME CHECKS PASS";RepairText.Foreground=Green;return;}
  if(_repairing||DateTime.Now<_nextRepair)return;
  if(_repairAttempts>=MaxRepairAttempts){RepairText.Text=$"WATCHDOG: REPAIR FAILED AFTER {_repairAttempts}/{MaxRepairAttempts} ATTEMPTS";RepairText.Foreground=Red;Visual("red");StateText.Text="STATUS: REPAIR REQUIRED";ActivityText.Text="Automatic recovery reached its retry limit. See live log.";return;}
  _repairing=true;_repairAttempts++;RepairText.Text=$"WATCHDOG: REPAIRING {_repairAttempts}/{MaxRepairAttempts} ? {string.Join(", ",missing.Select(Short))}";RepairText.Foreground=Yellow;Visual("yellow");StateText.Text="STATUS: SELF-REPAIR";ActivityText.Text="Restarting the trusted Prometheus startup sequence and verifying measured health.";OperationText.Text="Self-repair";Log($"Watchdog repair attempt {_repairAttempts}/{MaxRepairAttempts}: {string.Join(", ",missing.Select(Short))}");
  try{var r=await _bridge.RunAsync("start",root.TrimEnd('\\'));Log($"Repair launcher: {r.Status?.Message??r.StdErr}");if(missing.Contains("Load configured model")){RepairText.Text=$"WATCHDOG: REPAIRING {_repairAttempts}/{MaxRepairAttempts} ? warming local model";Log("Watchdog is warming the configured local model.");await _probe.WarmModelAsync();}for(int i=0;i<40;i++){await Task.Delay(500);var measured=await _probe.MeasureAsync(root);UpdateComponents(measured);ShowPlan(ProgressPlan.Startup,measured);if(new[]{"Start Ollama service","Load configured model","Open chat runtime"}.All(measured.Contains)){_repairAttempts=0;RepairText.Text="WATCHDOG: RECOVERED / RUNTIME HEALTHY";RepairText.Foreground=Green;Visual("yellow");StateText.Text="STATUS: RUNNING";ActivityText.Text="Watchdog recovery succeeded and all runtime checks passed.";OperationText.Text="Running";Log("Watchdog recovery succeeded.");return;}}_nextRepair=DateTime.Now.AddSeconds(Math.Min(30,5*_repairAttempts));RepairText.Text=$"WATCHDOG: RETRY {_repairAttempts}/{MaxRepairAttempts} FAILED ? NEXT CHECK {_nextRepair:HH:mm:ss}";RepairText.Foreground=_repairAttempts>=MaxRepairAttempts?Red:Yellow;Log("Watchdog repair attempt did not restore all runtime checks.");}finally{_repairing=false;}
 }

 void UpdateComponents(ISet<string> d){Set(SsdComponent,"SSD Detection",d.Contains("Verify Prometheus SSD identity"));Set(RuntimeComponent,"Runtime (Python)",d.Contains("Validate portable runtime"));Set(OllamaComponent,"Ollama Service",d.Contains("Start Ollama service"));Set(ModelComponent,"Model Load",d.Contains("Load configured model"));Set(MemoryComponent,"Memory DB",d.Contains("Verify local memory database"));Set(StoreComponent,"Model Store",d.Contains("Verify portable model store"));Set(SourceComponent,"Source Tree",d.Contains("Verify Prometheus source tree"));Set(ChatComponent,"Chat Runtime",d.Contains("Open chat runtime"));}
 void Set(System.Windows.Controls.TextBlock t,string name,bool ok){t.Text=$"● {name} — {(ok?"Ready":"Stopped / unavailable")}";t.Foreground=ok?Green:Yellow;}
 void ApplyStatus(BridgeResult r){var s=r.Status;if(s==null){Visual("red");StateText.Text="STATUS: BLOCKED";ActivityText.Text="Controller unavailable. Check details in the log.";OperationText.Text="Error";EjectText.Text="No";Log(r.StdErr.Length>0?r.StdErr:"Unable to parse controller response.");return;}Visual(s.Phase??"yellow");StateText.Text=$"STATUS: {(s.Phase=="green"?"READY":s.Phase?.ToUpperInvariant())}";ActivityText.Text=s.Message??"Prometheus controller";OperationText.Text=s.Phase=="green"?"Stopped":s.Phase=="red"?"Blocked":"Running / checking";EjectText.Text="No";Log($"Controller: {s.Message} {s.Activity}");StartButton.IsEnabled=s.Phase=="green";StopButton.IsEnabled=s.Phase!="green";}
 async void Start_Click(object sender,RoutedEventArgs e){if(_operationActive||_refreshing||_ejecting)return;SetDesired(true);_repairAttempts=0;_nextRepair=DateTime.MinValue;RepairText.Text="WATCHDOG: ARMED / STARTUP EXPECTED";RepairText.Foreground=Yellow;_startupFailed=false;_timer.Start();_operationActive=true;try{var root=_probe.FindPrometheusDrive();if(root==null){await RefreshAsync();return;}var sw=Stopwatch.StartNew();StartButton.IsEnabled=false;StopButton.IsEnabled=true;Visual("yellow");StateText.Text="STATUS: STARTING";ActivityText.Text="Measured startup checks are running.";OperationText.Text="Startup";EjectText.Text="No";ShowPlan(ProgressPlan.Startup);Log("Startup requested.");var r=await _bridge.RunAsync("start",root.TrimEnd('\\'));ApplyStatus(r);for(int i=0;i<60;i++){var done=await _probe.MeasureAsync();UpdateComponents(done);ShowPlan(ProgressPlan.Startup,done);if(done.Count==ProgressPlan.Startup.Length){sw.Stop();Visual("yellow");StateText.Text="STATUS: RUNNING";ActivityText.Text="All measured startup checks passed.";OperationText.Text="Running";EjectText.Text="No";ElapsedText.Text=$"Startup completed in {sw.Elapsed.TotalSeconds:F1} seconds.";StartupTimeText.Text=$"{sw.Elapsed.TotalSeconds:F1} seconds\n{DateTime.Now:g}";Log("All startup measurements passed.");return;}await Task.Delay(500);}sw.Stop();_startupFailed=true;ShowPlan(ProgressPlan.Startup,_lastCompleted);Visual("red");StateText.Text="STATUS: STARTUP INCOMPLETE";ElapsedText.Text="Startup checks timed out; incomplete rows are red.";Log("Startup measurement timed out.");}catch(Exception ex){Visual("red");EjectText.Text="No";Log(ex.Message);}finally{_operationActive=false;}}
 async void Stop_Click(object sender,RoutedEventArgs e){if(_operationActive||_refreshing||_ejecting)return;SetDesired(false);_repairAttempts=0;RepairText.Text="WATCHDOG: DISARMED / INTENTIONAL STOP";RepairText.Foreground=Muted;_operationActive=true;try{var root=_probe.FindPrometheusDrive();if(root==null){await RefreshAsync();return;}var sw=Stopwatch.StartNew();StartButton.IsEnabled=false;StopButton.IsEnabled=false;Visual("yellow");StateText.Text="STATUS: STOPPING / VERIFYING";ActivityText.Text="Graceful shutdown and eject-readiness checks are running.";OperationText.Text="Shutdown";EjectText.Text="No";var done=new HashSet<string>{"Request graceful chat shutdown"};ShowPlan(ProgressPlan.Shutdown,done);Log("Graceful shutdown requested.");var r=await _bridge.RunAsync("stop",root.TrimEnd('\\'));sw.Stop();if(r.Status?.Phase=="green"){foreach(var x in ProgressPlan.Shutdown)done.Add(x.Name);ShowPlan(ProgressPlan.Shutdown,done);Visual("green");StateText.Text="STATUS: READY";ActivityText.Text="Prometheus is stopped and verification passed.";OperationText.Text="Idle";EjectText.Text="No";ElapsedText.Text=$"Shutdown and verification completed in {sw.Elapsed.TotalSeconds:F1} seconds.";ShutdownTimeText.Text=$"{sw.Elapsed.TotalSeconds:F1} seconds\n{DateTime.Now:g}";VerifyTimeText.Text=$"Passed\n{DateTime.Now:g}";StartButton.IsEnabled=true;_timer.Stop();Log("Prometheus stopped. Use Safely Eject SSD to request Windows removal.");ActivityText.Text="Prometheus stopped; Windows has not released the SSD yet.";OperationText.Text="Stopped";}else{ApplyStatus(r);ElapsedText.Text="Shutdown blocked; see controller status and log.";Log("Shutdown verification did not reach green.");}}catch(Exception ex){Visual("red");EjectText.Text="No";Log(ex.Message);}finally{_operationActive=false;}}

 async void OpenChat_Click(object sender,RoutedEventArgs e){
  var root=_probe.FindPrometheusDrive();if(root==null){Log("Cannot open chat: Prometheus SSD is not mounted.");await RefreshAsync();return;}
  var launcher=Path.Combine(root,"START-PROMETHEUS-SSD.cmd");if(!File.Exists(launcher)){Log("Cannot open chat: SSD launcher is missing.");return;}
  try{Process.Start(new ProcessStartInfo("cmd.exe",$"/d /s /k \"\"{launcher}\" chat\""){WorkingDirectory=root,UseShellExecute=true});Log("Interactive Prometheus chat opened by user request.");}
  catch(Exception ex){Log("Unable to open chat: "+ex.Message);}
 }

 async void Eject_Click(object sender,RoutedEventArgs e){
 if(_operationActive||_refreshing||_ejecting)return;
 SetDesired(false);_repairAttempts=0;RepairText.Text="WATCHDOG: DISARMED / EJECT";RepairText.Foreground=Muted;_ejecting=true;_timer.Stop();EjectButton.IsEnabled=false;StartButton.IsEnabled=false;StopButton.IsEnabled=false;
 try{
 var root=_probe.FindPrometheusDrive();if(root==null){Log("No mounted Prometheus SSD to eject.");return;}
 Visual("yellow");StateText.Text="STATUS: PREPARING EJECT";ActivityText.Text="Closing chat and verifying databases before Windows removal.";OperationText.Text="Safe eject";EjectText.Text="No";
 var result=await _bridge.RunAsync("stop",root.TrimEnd('\\'));
 Log(result.Status?.Activity??result.StdErr);
 if(result.ExitCode!=0||result.Status?.Phase!="green"){Visual("red");StateText.Text="STATUS: EJECT BLOCKED";ActivityText.Text=result.Status?.Message??"Graceful shutdown failed.";return;}
 var script=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Prometheus","Prometheus-Eject-Orchestrator.ps1");
 if(!File.Exists(script))throw new FileNotFoundException("Host eject orchestrator missing.",script);
 var psi=new ProcessStartInfo("powershell.exe"){UseShellExecute=true,WindowStyle=ProcessWindowStyle.Hidden};
 foreach(var arg in new[]{"-NoProfile","-ExecutionPolicy","Bypass","-File",script,"-Drive",root.TrimEnd('\\')})psi.ArgumentList.Add(arg);
 Process.Start(psi);
 Log("Host eject handoff started. Controller will close; Desktop Commander will reconnect after Windows release or veto.");
 ActivityText.Text="Handoff started. Watch Windows notification. Do not unplug until Windows confirms removal.";
 OperationText.Text="Host handoff";
 await Task.Delay(500);
 Close();
 }catch(Exception ex){Visual("red");StateText.Text="STATUS: EJECT ERROR";EjectText.Text="No";Log(ex.Message);}
 finally{_ejecting=false;EjectButton.IsEnabled=true;StartButton.IsEnabled=_probe.FindPrometheusDrive()!=null;}
 }

 void ShowInfo(string title,string content){
  var window=new Window{Title=title,Width=760,Height=520,MinWidth=500,MinHeight=300,Owner=this,WindowStartupLocation=WindowStartupLocation.CenterOwner,Background=new SolidColorBrush(Color.FromRgb(7,17,28))};
  var panel=new System.Windows.Controls.DockPanel{Margin=new Thickness(16)};
  var close=new System.Windows.Controls.Button{Content="Close",Height=38,Width=100,HorizontalAlignment=HorizontalAlignment.Right,Margin=new Thickness(0,12,0,0)};
  close.Click+=(_,_)=>window.Close();System.Windows.Controls.DockPanel.SetDock(close,System.Windows.Controls.Dock.Bottom);panel.Children.Add(close);
  panel.Children.Add(new System.Windows.Controls.TextBox{Text=content,IsReadOnly=true,TextWrapping=TextWrapping.Wrap,VerticalScrollBarVisibility=System.Windows.Controls.ScrollBarVisibility.Auto,Background=new SolidColorBrush(Color.FromRgb(13,27,41)),Foreground=System.Windows.Media.Brushes.White,Padding=new Thickness(12)});
  window.Content=panel;window.Show();
 }
 void Logs_Click(object sender,RoutedEventArgs e){
  try{
   var local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
   var paths=new[]{Path.Combine(local,"RemovableMediaWorkStatus","activity-D.log"),Path.Combine(local,"Prometheus","Diagnostics","eject-history.jsonl"),Path.Combine(local,"Prometheus","Diagnostics","usb-supervisor.log")};
   var report=string.Join(Environment.NewLine+Environment.NewLine,paths.Select(p=>{
    if(!File.Exists(p))return p+Environment.NewLine+"(No log found)";
    using var fs=new FileStream(p,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete);
    fs.Seek(Math.Max(0,fs.Length-16000),SeekOrigin.Begin);
    using var sr=new StreamReader(fs);return p+Environment.NewLine+sr.ReadToEnd();
   }));
   ShowInfo("Prometheus — Diagnostic Logs",report);Log("Diagnostic logs opened.");
  }catch(Exception ex){ShowInfo("Logs error",ex.ToString());Log("Logs error: "+ex.Message);}
 }
 void Settings_Click(object sender,RoutedEventArgs e){
  var root=_probe.FindPrometheusDrive();
  ShowInfo("Prometheus — Settings", "Controller settings and safety information"+Environment.NewLine+Environment.NewLine+
  "SSD: "+(root??"Not connected")+Environment.NewLine+
  "Auto-start desired: "+_desiredRunning+Environment.NewLine+
  "Watchdog: "+(_desiredRunning?"Armed":"Disarmed")+Environment.NewLine+
  "Windows released SSD: "+EjectText.Text+Environment.NewLine+Environment.NewLine+
  "The controller does not change USB policies or format drives. Ejection is allowed only after Windows confirms release.");
  Log("Settings opened.");
 }
 void Vault_Click(object sender,RoutedEventArgs e){
  var root=_probe.FindPrometheusDrive();
  if(root==null){ShowInfo("Knowledge Vault","Prometheus SSD is not connected.");return;}
  var path=Path.Combine(root,"Prometheus-Resources","Knowledge");
  if(!Directory.Exists(path)){ShowInfo("Knowledge Vault","Knowledge folder not found: "+path);return;}
  try{Process.Start(new ProcessStartInfo("explorer.exe"){ArgumentList={path},UseShellExecute=true});Log("Knowledge Vault opened.");}
  catch(Exception ex){ShowInfo("Knowledge Vault error",ex.Message);}
 }

}