using System.IO;
using System.Text.Json;
using System.Windows.Threading;

namespace Prometheus.DriveController;
public partial class MainWindow {
    readonly DispatcherTimer _arrivalTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    string? _lastArrivalSeen;
    void InitializeArrivalObserver() {
        if (Environment.GetCommandLineArgs().Contains("--verify-layout")) return;
        // Internal-disk status only. Never re-open the SSD during a departure pause.
        _arrivalTimer.Tick += async (_, _) => {
            if (_ejecting || _operationActive || _refreshing || StartupProbe.EjectSuppressed) return;
            try {
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
