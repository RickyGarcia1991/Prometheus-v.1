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
        return ReadEjectState(path);
    }
    bool ReadEjectState(string path, Func<string,string>? read = null) {
        try {
        using var state = JsonDocument.Parse((read ?? File.ReadAllText)(path));
        string phase = state.RootElement.GetProperty("phase").GetString() ?? "unknown";
        string message = state.RootElement.TryGetProperty("message", out var text) ? text.GetString() ?? "" : "Services are paused while shutdown and Windows removal are verified.";
        ApplyDepartureState(phase, message);
        } catch (FileNotFoundException) { return false; }
        catch (DirectoryNotFoundException) { return false; }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException || ex is System.Security.SecurityException || ex is JsonException || ex is InvalidOperationException || ex is KeyNotFoundException) {
            ApplyDepartureState("blocked", "Departure status is unavailable; Windows removal is not confirmed.");
        }
        return true;
    }
    bool ApplyArrivalState(string? phase, string? message) {
        if (phase == "starting") {
            _timer.Stop(); Visual("yellow"); StateText.Text = "STATUS: STARTING PROMETHEUS";
            ActivityText.Text = "Verifying the SSD, saved memory and interface. The workspace opens when these checks finish.";
            OperationText.Text = "Automatic startup"; EjectText.Text = "No";
            StartButton.IsEnabled = false; StopButton.IsEnabled = false; EjectButton.IsEnabled = false;
            if (!StartupProbe.VerificationMode) { Show(); WindowState = WindowState.Normal;
                bool wasTopmost = Topmost; Topmost = true; Activate(); Topmost = wasTopmost; }
            return false;
        }
        if (phase == "blocked") {
            _timer.Stop(); Visual("red"); StateText.Text = "STATUS: STARTUP BLOCKED";
            ActivityText.Text = message ?? "See the startup log.";
            EjectText.Text = "No"; StartButton.IsEnabled = true; EjectButton.IsEnabled = true; StopButton.IsEnabled = false;
            return false;
        }
        if (phase != "ready") {
            _lastArrivalSeen = null;
            _timer.Stop(); Visual("red"); StateText.Text = "STATUS: ARRIVAL UNKNOWN";
            ActivityText.Text = "Arrival status is unavailable; Windows removal is not confirmed.";
            EjectText.Text = "No"; StartButton.IsEnabled = false; StopButton.IsEnabled = false; EjectButton.IsEnabled = false;
            return false;
        }
        EjectText.Text = "No";
        StartButton.Content = "Start Prometheus"; EjectButton.Content = "Eject & verify";
        EjectButton.IsEnabled = true;
        return true;
    }
    bool ApplyArrivalRecord(string? phase, string? updated, string? message) {
        if (updated == null) { ApplyArrivalState(null,null); return false; }
        string identity = phase + "|" + updated;
        if (identity == _lastArrivalSeen) return false;
        _lastArrivalSeen = identity;
        return ApplyArrivalState(phase,message);
    }
    void InitializeArrivalObserver() {
        if (Environment.GetCommandLineArgs().Contains("--verify-layout")) return;
        // Internal-disk status only. Never re-open the SSD during a departure pause.
        _arrivalTimer.Tick += async (_, _) => {
            if (_ejecting || _operationActive || _refreshing) return;
            try {
                if (ObserveEjectState()) return;
                string path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Prometheus", "arrival-status.json");
                using var state = JsonDocument.Parse(File.ReadAllText(path));
                string? phase = state.RootElement.GetProperty("phase").GetString();
                string? updated = state.RootElement.GetProperty("updated").GetString();
                var message = state.RootElement.TryGetProperty("message", out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
                if (!ApplyArrivalRecord(phase, updated, message)) return;
                _refreshing = true;
                await RefreshAsync();
                _timer.Start();
            } catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
            catch (Exception ex) { ApplyArrivalState(null,null); Log("Arrival status: " + ex.Message); }
            finally { _refreshing = false; }
        };
        _arrivalTimer.Start();
    }
}
