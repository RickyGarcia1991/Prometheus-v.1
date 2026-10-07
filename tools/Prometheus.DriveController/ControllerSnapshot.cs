namespace Prometheus.DriveController;
public sealed record ControllerSnapshot(ControllerState State, int Progress, string Activity, IReadOnlyList<ProgressStep> Steps);
