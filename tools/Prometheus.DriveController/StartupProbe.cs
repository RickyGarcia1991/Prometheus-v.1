using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
namespace Prometheus.DriveController;

public sealed class StartupProbe
{
    public static bool EjectSuppressed => File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Prometheus", "eject-mode.json"));
    DateTime _checkedAt = DateTime.MinValue;
    string? _checkedRoot;
    JsonElement? _readiness;
    JsonElement? _profile;
    DateTime _profileAt = DateTime.MinValue;
    static readonly HttpClient Http = new(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
        { Timeout = TimeSpan.FromSeconds(3) };

    public string? FindPrometheusDrive()
    {
        foreach (var drive in DriveInfo.GetDrives())
            try { if (drive.IsReady && drive.VolumeLabel.Equals("Prometheus-2TB", StringComparison.OrdinalIgnoreCase)) return drive.RootDirectory.FullName; } catch { }
        return null;
    }

    public (bool Healthy, string Message) DriveHealth(string root)
    {
        try {
            var drive = new DriveInfo(root);
            if (!drive.IsReady || drive.VolumeLabel != "Prometheus-2TB") return (false, "Prometheus volume is not readable.");
            if (!File.Exists(Path.Combine(root, "PROMETHEUS-SSD-STATUS.json"))) return (false, "Release metadata is missing.");
            return (true, "Volume readable. Hardware health requires Windows disk diagnostics.");
        } catch (Exception ex) { return (false, ex.Message); }
    }

    public string? FindActiveRelease(string root)
    {
        try {
            using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "PROMETHEUS-SSD-STATUS.json")));
            var name = Path.GetFileName(doc.RootElement.GetProperty("code_release").GetString()!.TrimEnd('\\', '/'));
            if (!name.StartsWith("Prometheus-v", StringComparison.Ordinal) || name.Contains("..")) return null;
            var release = Path.Combine(root, name);
            return File.Exists(Path.Combine(release, "prometheus.py")) ? release : null;
        } catch { return null; }
    }

    public static async Task<JsonElement?> RunJsonAsync(string python, string directory, params string[] args)
    {
        if (EjectSuppressed) return null;
        var info = new ProcessStartInfo(python) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = directory };
        info.ArgumentList.Add("-B");
        foreach (var arg in args) info.ArgumentList.Add(arg);
        using var process = Process.Start(info);
        if (process == null) return null;
        var output = process.StandardOutput.ReadToEndAsync();
        var error = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(20));
        try { await process.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException) { if (!process.HasExited) process.Kill(); return null; }
        var text = await output; await error;
        try { using var doc = JsonDocument.Parse(text); return doc.RootElement.Clone(); } catch { return null; }
    }

    public async Task<JsonElement?> HardwareProfileAsync(string root, bool refresh = false)
    {
        if (EjectSuppressed) return null;
        if (!refresh && _checkedRoot == root && DateTime.UtcNow - _profileAt < TimeSpan.FromSeconds(60)) return _profile;
        var release = FindActiveRelease(root);
        if (release == null) return null;
        _profile = await RunJsonAsync(Path.Combine(root, "Prometheus-Resources", "Python", "python.exe"),
            AppContext.BaseDirectory, Path.Combine(release, "prometheus.py"), "inspect-host", "--json");
        _profileAt = DateTime.UtcNow;
        return _profile;
    }

    public async Task<HashSet<string>> MeasureAsync(string? drive = null)
    {
        var done = new HashSet<string>();
        if (EjectSuppressed) return done;
        var root = drive ?? FindPrometheusDrive();
        if (root == null || !DriveHealth(root).Healthy) return done;
        done.Add("Verify Prometheus SSD identity");
        if (_checkedRoot != root || DateTime.UtcNow - _checkedAt > TimeSpan.FromSeconds(60)) {
            _readiness = await RunJsonAsync(Path.Combine(root, "Prometheus-Resources", "Python", "python.exe"),
                AppContext.BaseDirectory, Path.Combine(root, "Prometheus-Resources", "Tools", "portable-readiness.py"), "--root", root);
            _checkedRoot = root; _checkedAt = DateTime.UtcNow;
        }
        if (_readiness is JsonElement ready) {
            bool Has(string key) => ready.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.True;
            if (Has("python_present") && Has("ollama_present")) done.Add("Validate portable runtime");
            if (Has("core_integrity")) done.Add("Verify Prometheus source tree");
            if (Has("model_present")) done.Add("Verify portable model store");
            if (ready.TryGetProperty("memory_integrity", out var memory) && memory.GetString() == "ok") done.Add("Verify local memory database");
        }
        if (HasDriveProcess("ollama", root)) {
            try {
                using var loaded = JsonDocument.Parse(await Http.GetStringAsync("http://127.0.0.1:11434/api/ps"));
                done.Add("Start Ollama service");
                var profile = await HardwareProfileAsync(root);
                var model = profile?.GetProperty("recommended_model").GetString();
                if (model != null && loaded.RootElement.TryGetProperty("models", out var models) &&
                    models.EnumerateArray().Any(m => m.TryGetProperty("name", out var name) && name.GetString() == model))
                    done.Add("Load configured model");
            } catch { }
        }
        var sessionLock = Path.Combine(root, "Prometheus-Data", ".memory-session.lock");
        if (File.Exists(sessionLock) && HasDriveProcess("python", root)) {
            try { using var stream = new FileStream(sessionLock, FileMode.Open, FileAccess.ReadWrite, FileShare.None); }
            catch (IOException) { done.Add("Open chat runtime"); }
        }
        return done;
    }

    public async Task<bool> WarmModelAsync()
    {
        if (EjectSuppressed) return false;
        var root = FindPrometheusDrive();
        if (root == null) return false;
        var profile = await HardwareProfileAsync(root, true);
        if (profile is not JsonElement p || !p.TryGetProperty("recommended_model", out var selected) || selected.ValueKind != JsonValueKind.String) return false;
        var model = selected.GetString();
        var context = p.GetProperty("recommended_context").GetInt32();
        var threads = p.TryGetProperty("recommended_cpu_threads", out var t) && t.ValueKind == JsonValueKind.Number ? t.GetInt32() : 2;
        try {
            using var client = new HttpClient(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(120) };
            var payload = JsonSerializer.Serialize(new { model, keep_alive = "5m", stream = false, options = new { num_ctx = context, num_thread = threads } });
            using var content = new StringContent(payload, Encoding.UTF8, "application/json");
            using var response = await client.PostAsync("http://127.0.0.1:11434/api/generate", content);
            return response.IsSuccessStatusCode;
        } catch { return false; }
    }
    static bool HasDriveProcess(string name, string root)
    {
        bool found = false;
        foreach (var process in Process.GetProcessesByName(name)) {
            using (process) try { if (process.MainModule?.FileName?.StartsWith(root, StringComparison.OrdinalIgnoreCase) == true) found = true; } catch { }
        }
        return found;
    }
}
