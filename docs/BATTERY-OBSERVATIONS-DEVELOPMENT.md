# Device battery observations — development slice

The resource status panel can now show the observed battery percentage change across this computer over a bounded interval. This is whole-device charge telemetry, including other applications and Windows activity. It is not a job energy measurement, predicted remaining runtime or new permission to run on battery.

`ResourceProbe.snapshot()` keeps its existing on-demand sampling, ten-second cache, lock and independent returned copies. There is no polling thread, new dependency, remote call, battery stress workload or OS configuration change. The existing native Windows/Linux power readers supply charge percentage and external-power state.

An interval requires valid on-battery observations for at least 60 seconds, permits at most 60 seconds between observations, and lasts at most 900 seconds. These bounds describe observation continuity, not calibrated power-policy thresholds. AC power, unknown power, unavailable/invalid charge, an increase in charge, a backward monotonic clock or an excessive gap resets the interval. Gap/clock/increase/window resets use the current valid on-battery reading as the next baseline. Unknown readings and AC discard the baseline entirely. A new process always starts without an old interval.

The additional `battery_observation` object contains:

- `status`: `observed` or `unavailable`.
- `scope`: always `whole_device`.
- `percentage_points`: nonnegative observed decrease, or null.
- `interval_seconds`: measured monotonic interval from 60 through 900 seconds, or null.
- `reason`: bounded, application-authored explanation.

The existing `battery_drain_percent` field mirrors the qualified device-level percentage-point delta; its source describes that scope. It must not be copied into a job's `Cost.battery_percent` or workload history. The Activity panel consumes the complete observation object, validates its numeric bounds, and uses literal text. Zero means “no percentage change observed,” because coarse percentage readings cannot establish zero energy use. Disconnection removes the current readings and labels them stale.

This interval is based on sampled readings; power transitions between samples may not be observed. No electrical energy, battery capacity degradation or per-process attribution is inferred. Existing task admission, signed remote advertisement schemas, worker protocol versions and resource history remain unchanged. Task-specific battery drain remains unmeasured and jobs with unknown battery budgets remain deferred under the existing policy.

Development verification includes first-sample/short-interval unknowns, discharge and unchanged charge, invalid values including oversized integers, AC and unknown-power resets, charge increases, clock rollback, missing/stale sample intervals, window expiry, restart, cache isolation, concurrent readers, and a real scheduler integration check proving device telemetry cannot authorize or calibrate a job. JS contracts cover observed and unavailable text, malformed observations, literal markup and disconnected state. Native discharge behavior and browser presentation require normal runtime qualification; no hardware drain test was run for this change.
