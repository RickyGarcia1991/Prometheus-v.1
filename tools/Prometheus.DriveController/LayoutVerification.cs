using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
namespace Prometheus.DriveController;
public partial class MainWindow {
 static IEnumerable<T> Descendants<T>(DependencyObject root) where T:DependencyObject {
  for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++){var child=VisualTreeHelper.GetChild(root,i);if(child is T t)yield return t;foreach(var nested in Descendants<T>(child))yield return nested;}
 }
 async Task VerifyLayoutAsync(string directory) {
  await Task.Delay(100);
  try{
   Directory.CreateDirectory(directory);
   if(_timer.IsEnabled || _arrivalTimer.IsEnabled)throw new InvalidOperationException("Live timers must be disabled during fixture verification.");
   if((await _probe.MeasureAsync()).Count!=0 || await _probe.WarmModelAsync() || await StartupProbe.RunJsonAsync("must-not-execute",directory) != null)throw new Exception("Fixture mode must suppress live probes.");
   try{_probe.FindPrometheusDrive();throw new Exception("Fixture mode permitted drive discovery.");}catch(InvalidOperationException){}
   if(Descendants<Button>(this).Count(x=>x.Content?.ToString()=="Eject & verify")!=1 || Descendants<Button>(this).Any(x=>x.Content?.ToString()=="Prepare for Windows eject"))throw new Exception("Expected exactly one eject action.");
   foreach(var phase in new[]{"stopping","blocked","released","unknown"}){ApplyDepartureState(phase,"Layout verification");if((EjectText.Text=="Yes")!=(phase=="released"))throw new Exception("Only Windows release may show safe removal.");}
   ApplyArrivalState("starting",null);
   if(StartButton.IsEnabled || StopButton.IsEnabled || EjectButton.IsEnabled || EjectText.Text!="No")throw new Exception("Arrival startup must disable actions and safe removal.");
   ApplyArrivalState("blocked","Generated startup veto");
   if(!StartButton.IsEnabled || !EjectButton.IsEnabled || StopButton.IsEnabled || ActivityText.Text!="Generated startup veto")throw new Exception("Blocked arrival must permit retry and preserve the reason.");
   if(!ApplyArrivalState("ready",null) || StartButton.Content.ToString()!="Start Prometheus" || EjectButton.Content.ToString()!="Eject & verify")throw new Exception("Ready arrival must restore normal labels.");
   if(ApplyArrivalState("unknown",null))throw new Exception("Unknown arrival must not request a live refresh.");
   var marker=Path.Combine(directory,"generated-eject-state.json");
   try {
    foreach(var invalid in new[]{"{", "{}", "{\"phase\":42}"}){
     ApplyDepartureState("released","Generated release");File.WriteAllText(marker,invalid);
     if(!ReadEjectState(marker) || EjectText.Text!="No")throw new Exception("Malformed departure must clear old safe-removal status.");
    }
    File.WriteAllText(marker,"{\"phase\":\"released\",\"message\":\"Generated release\"}");
    if(!ReadEjectState(marker) || EjectText.Text!="Yes")throw new Exception("Released fixture not displayed.");
   } finally { if(File.Exists(marker))File.Delete(marker); }
   if(ReadEjectState(marker))throw new Exception("Missing fixture must not be treated as a departure.");
   foreach(var denied in new Exception[]{new UnauthorizedAccessException(),new System.Security.SecurityException(),new IOException()}){
    ApplyDepartureState("released","Generated release");
    if(!ReadEjectState(marker,_=>throw denied) || EjectText.Text!="No" || StateText.Text!="STATUS: EJECT BLOCKED")throw new Exception("Unreadable departure must clear safe removal and block probing.");
    if(!StartupProbe.DepartureMarkerPresent(marker,_=>throw denied))throw new Exception("Unreadable departure must suppress startup probes.");
   }
   if(StartupProbe.DepartureMarkerPresent(marker))throw new Exception("Missing departure fixture should not suppress probes.");
   ApplyDepartureState("released","Generated release");ApplyArrivalState("unknown",null);
   if(EjectText.Text!="No" || StartButton.IsEnabled || StopButton.IsEnabled || EjectButton.IsEnabled)throw new Exception("Unknown arrival must clear safe removal and disable actions.");
   if(!ApplyArrivalRecord("ready","fixture-time",null) || ApplyArrivalRecord("ready","fixture-time",null))throw new Exception("Ready observation should refresh once.");
   ApplyArrivalState(null,null);
   if(!ApplyArrivalRecord("ready","fixture-time",null))throw new Exception("Valid arrival must recover after an unreadable observation even with the same timestamp.");
   if(_bridge.ControllerPath!="")throw new Exception("Fixture bridge must bypass controller-path discovery.");
   try{await _bridge.RunAsync("status");throw new Exception("Fixture bridge allowed controller execution.");}catch(InvalidOperationException){}
   ApplyDepartureState("stopping","Layout verification");
   StartButton.IsEnabled=true;EjectButton.IsEnabled=true;
   _operationActive=true;
   ShowPlan(ProgressPlan.Startup,new HashSet<string>{ProgressPlan.Startup[0].Name,ProgressPlan.Startup[1].Name});
   UpdateLayout();
   var rows=StepsList.Items.Cast<StepRow>().ToArray();
   if(rows.Length!=8||rows.Count(x=>x.Color==Green)!=2||rows.Count(x=>x.Color==Yellow)!=6)throw new Exception("Passed / checking state mapping failed.");
   _startupFailed=true;ShowPlan(ProgressPlan.Startup,new HashSet<string>{ProgressPlan.Startup[0].Name,ProgressPlan.Startup[1].Name});UpdateLayout();
   if(StepsList.Items.Cast<StepRow>().Count(x=>x.Color==Red)!=6)throw new Exception("Failed check state mapping failed.");
   _startupFailed=false;_operationActive=false;
   var measured=new HashSet<string>{ProgressPlan.Startup[0].Name,ProgressPlan.Startup[1].Name};ShowPlan(ProgressPlan.Startup,measured);UpdateComponents(measured);UpdateLayout();
   if(EjectText.Text!="No")throw new Exception("Windows release must default to No.");
   foreach(var width in new[]{1080d,1280d}){
    Width=width;UpdateLayout();
    var numbers=Descendants<TextBlock>(StepsList).Where(x=>x.Text.EndsWith('%')).ToArray();
    var positions=numbers.Select(x=>x.TransformToAncestor(StepsList).Transform(new Point(0,0)).X).ToArray();
    if(numbers.Length!=8||positions.Max()-positions.Min()>0.5)throw new Exception($"Percentage columns do not align at width {width}.");
   }
   var bitmap=new RenderTargetBitmap((int)ActualWidth,(int)ActualHeight,96,96,PixelFormats.Pbgra32);bitmap.Render(this);
   var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap));using(var stream=File.Create(Path.Combine(directory,"controller-layout.png")))encoder.Save(stream);
   File.WriteAllText(Path.Combine(directory,"controller-layout-test.txt"),"PASS: fixture-only; live timers disabled; one eject button; departure stopping/blocked/released/unknown; arrival starting/blocked/ready/unknown; malformed departure clears safe-removal claim; 8 aligned percentages at widths 1080 and 1280; green/yellow/red states; Windows release defaults to No. No physical eject or startup was tested.");
   if(_timer.IsEnabled || _arrivalTimer.IsEnabled)throw new InvalidOperationException("Fixture verification enabled a live timer.");
   Application.Current.Shutdown(0);
  }catch(Exception ex){File.WriteAllText(Path.Combine(directory,"controller-layout-test.txt"),"FAIL: "+ex);Application.Current.Shutdown(1);}
 }
}
