using System.Windows;
namespace Prometheus.DriveController;
public partial class MainWindow : Window
{
    private ControllerState _state=ControllerState.Ready;
    public MainWindow(){InitializeComponent(); ShowPlan(ProgressPlan.Startup);}
    private void ShowPlan(IEnumerable<ProgressStep> steps){var s=steps.ToArray(); StepsList.ItemsSource=s.Select(x=>$"○  {x.Name}   [{x.Weight}%]"); OverallProgress.Value=ProgressPlan.Percent(s); PercentText.Text=$"{OverallProgress.Value:0}%";}
    private void Start_Click(object sender,RoutedEventArgs e){_state=ControllerState.Starting; StateText.Text="PROMETHEUS IS STARTING"; ActivityText.Text="Waiting for measured startup checks..."; StartButton.IsEnabled=false; StopButton.IsEnabled=true; ShowPlan(ProgressPlan.Startup);}
    private void Stop_Click(object sender,RoutedEventArgs e){_state=ControllerState.Stopping; StateText.Text="STOPPING PROMETHEUS — VERIFYING"; ActivityText.Text="Graceful shutdown and eject-readiness verification in progress..."; StartButton.IsEnabled=false; StopButton.IsEnabled=false; ShowPlan(ProgressPlan.Shutdown);}
}
