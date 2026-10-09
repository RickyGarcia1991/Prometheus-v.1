using System.IO;
using System.Text.Json;
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
                if (state.RootElement.GetProperty("phase").GetString() != "ready") return;
                string? updated = state.RootElement.GetProperty("updated").GetString();
                if (updated == null || updated == _lastArrivalSeen) return;
                _lastArrivalSeen = updated;
                _refreshing = true;
                StartButton.Content = "Start Prometheus";
                EjectButton.Content = "Eject & verify";
                await RefreshAsync();
                _timer.Start();
            } catch (IOException) { }
            catch (Exception ex) { Log("Arrival status: " + ex.Message); }
            finally { _refreshing = false; }
        };
        _arrivalTimer.Start();
    }
}
