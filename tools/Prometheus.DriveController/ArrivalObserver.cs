using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Threading;

namespace Prometheus.DriveController;
public partial class MainWindow {
    readonly DispatcherTimer _arrivalTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    string? _lastArrivalSeen;
    void ApplyDepartureState(string phase, string message) {
        _timer.Stop();
        bool released = phase == "released";
        bool blocked = phase == "blocked";
        Visual(released ? "green" : blocked ? "red" : "yellow");
        StateText.Text = released ? "SAFE TO REMOVE" : blocked ? "STATUS: EJECT BLOCKED" : "STATUS: PREPARING EJECT";
        EjectText.Text = released ? "Yes" : "No";
        OperationText.Text = released ? "Ejected" : blocked ? "Eject blocked" : "Safe eject";
        ActivityText.Text = message;
        EjectButton.Content = blocked ? "Retry eject" : "Eject & verify";
        EjectButton.IsEnabled = blocked;
        StartButton.Content = "Resume Prometheus";
        StartButton.IsEnabled = blocked;
        StopButton.IsEnabled = false;
    }
    bool ObserveEjectState() {
        string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Prometheus", "eject-mode.json");
        if (!File.Exists(path)) return false;
        using var state = JsonDocument.Parse(File.ReadAllText(path));
        string phase = state.RootElement.GetProperty("phase").GetString() ?? "unknown";
        string message = state.RootElement.TryGetProperty("message", out var text) ? text.GetString() ?? "" : "Services are paused while shutdown and Windows removal are verified.";
        ApplyDepartureState(phase, message);
        return true;
    }
    void InitializeArrivalObserver() {
        if (Environment.GetCommandLineArgs().Contains("--verify-layout")) return;
        // Internal-disk status only. Never re-open the SSD during a departure pause.
        _arrivalTimer.Tick += async (_, _) => {
            if (_ejecting || _operationActive || _refreshing) return;
            try {
                if (ObserveEjectState()) return;
                string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Prometheus", "arrival-status.json");
                if (!File.Exists(path)) return;
                using var state = JsonDocument.Parse(File.ReadAllText(path));
                string? phase = state.RootElement.GetProperty("phase").GetString();
                string? updated = state.RootElement.GetProperty("updated").GetString();
                string identity = phase + "|" + updated;
                if (updated == null || identity == _lastArrivalSeen) return;
                _lastArrivalSeen = identity;
                if (phase == "starting") {
                    _timer.Stop();
                    Visual("yellow"); StateText.Text = "STATUS: STARTING PROMETHEUS";
                    ActivityText.Text = "Verifying the SSD, saved memory and interface. The workspace opens when these checks finish.";
                    OperationText.Text = "Automatic startup"; EjectText.Text = "No";
                    StartButton.IsEnabled = false; StopButton.IsEnabled = false; EjectButton.IsEnabled = false;
                    Show(); WindowState = WindowState.Normal;
                    bool wasTopmost = Topmost; Topmost = true; Activate(); Topmost = wasTopmost;
                    return;
                }
                if (phase == "blocked") {
                    _timer.Stop(); Visual("red"); StateText.Text = "STATUS: STARTUP BLOCKED";
                    ActivityText.Text = state.RootElement.TryGetProperty("message", out var message) ? message.GetString() : "See the startup log.";
                    EjectText.Text = "No"; StartButton.IsEnabled = true; EjectButton.IsEnabled = true;
                    return;
                }
                if (phase != "ready") return;
                _refreshing = true;
                StartButton.Content = "Start Prometheus";
                EjectButton.Content = "Eject & verify";
                EjectButton.IsEnabled = true;
                await RefreshAsync();
                _timer.Start();
            } catch (IOException) { }
            catch (Exception ex) { Log("Arrival status: " + ex.Message); }
            finally { _refreshing = false; }
        };
        _arrivalTimer.Start();
    }
}
