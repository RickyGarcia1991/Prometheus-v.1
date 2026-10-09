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
  await Task.Delay(3000);_timer.Stop();
  try{
   Directory.CreateDirectory(directory);
   if(Descendants<Button>(this).Count(x=>x.Content?.ToString()=="Eject & verify")!=1 || Descendants<Button>(this).Any(x=>x.Content?.ToString()=="Prepare for Windows eject"))throw new Exception("Expected exactly one eject action.");
   foreach(var phase in new[]{"stopping","blocked","released"}){ApplyDepartureState(phase,"Layout verification");if((EjectText.Text=="Yes")!=(phase=="released"))throw new Exception("Only Windows release may show safe removal.");}
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
   var measured=await _probe.MeasureAsync();ShowPlan(ProgressPlan.Startup,measured);UpdateComponents(measured);UpdateLayout();
   if(EjectText.Text!="No")throw new Exception("Windows release must default to No.");
   foreach(var width in new[]{1080d,1280d}){
    Width=width;UpdateLayout();
    var numbers=Descendants<TextBlock>(StepsList).Where(x=>x.Text.EndsWith('%')).ToArray();
    var positions=numbers.Select(x=>x.TransformToAncestor(StepsList).Transform(new Point(0,0)).X).ToArray();
    if(numbers.Length!=8||positions.Max()-positions.Min()>0.5)throw new Exception($"Percentage columns do not align at width {width}.");
   }
   var bitmap=new RenderTargetBitmap((int)ActualWidth,(int)ActualHeight,96,96,PixelFormats.Pbgra32);bitmap.Render(this);
   var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(bitmap));using(var stream=File.Create(Path.Combine(directory,"controller-layout.png")))encoder.Save(stream);
   File.WriteAllText(Path.Combine(directory,"controller-layout-test.txt"),"PASS: one eject button; only released state shows safe removal; 8 aligned percentages at widths 1080 and 1280; green/yellow/red states; Windows release defaults to No.");
   Application.Current.Shutdown(0);
  }catch(Exception ex){File.WriteAllText(Path.Combine(directory,"controller-layout-test.txt"),"FAIL: "+ex);Application.Current.Shutdown(1);}
 }
}
