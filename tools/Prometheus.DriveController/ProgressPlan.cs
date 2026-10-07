namespace Prometheus.DriveController;
public static class ProgressPlan
{
    public static readonly ProgressStep[] Startup =
    [
        new("Verify Prometheus SSD identity", 10), new("Validate portable runtime", 10),
        new("Start Ollama service", 15), new("Load configured model", 25),
        new("Verify Knowledge Vault databases", 10), new("Verify resource sources", 10),
        new("Verify local index", 10), new("Open chat runtime", 10)
    ];
    public static readonly ProgressStep[] Shutdown =
    [
        new("Request graceful chat shutdown", 10), new("Confirm zero active chat clients", 15),
        new("Verify SQLite integrity", 20), new("Stop Ollama and model workers", 15),
        new("Confirm no Prometheus process references SSD", 15), new("Check PnP removal blockers", 15),
        new("Check UASP removal state", 10)
    ];
    public static int Percent(IEnumerable<ProgressStep> steps) { var s=steps.ToArray(); var total=s.Sum(x=>x.Weight); return total==0?0:(int)Math.Round(100.0*s.Where(x=>x.Complete).Sum(x=>x.Weight)/total); }
}
