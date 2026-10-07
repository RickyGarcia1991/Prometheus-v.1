namespace Prometheus.DriveController;
public sealed record ProgressStep(string Name, int Weight, bool Complete = false, string Detail = "");
