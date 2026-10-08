# Recovery reliability checkpoint — 2026-10-08

This checkpoint preserves the internal-drive watchdog and Task Scheduler diagnostic-enablement scripts. The scripts run on the Windows host and are not part of the removable SSD release.

## Verified

- Three continuous tasks (USB Reconnect Supervisor, Recovery Evidence Recorder, Volume Watcher) were configured for unlimited runtime and 3 restart attempts spaced 1 minute apart.
- An independent `Prometheus Recovery Watchdog` task checks the services every 2 minutes.
- `Prometheus Recovery Watchdog At Logon` performs an additional check when the user signs in.
- Watchdog restarts stopped recovery tasks, records previous state and last task result, and appends samples to `reliability-observations.jsonl`.
- Watchdog log and reliability-observation log rotate at 5 MiB, retaining up to 3 archived copies; rollover itself has not been tested.
- Controlled Evidence Recorder stop and watchdog restart passed. Task Scheduler Operational event log was enabled and recorded task start/action/finish events.
- Intentional-close flag remained present; controller was not launched.

## Limitations

- The historical reason for two task terminations (`0xC000013A`) is unconfirmed because Task Scheduler Operational logging was disabled at the time.
- 48-hour stability certification is pending observation, not a current PASS.
- Watchdog task registration and reliability settings are machine-local Task Scheduler configuration; this checkpoint contains scripts, not an automatic installation routine.
- No private diagnostic logs, local certificates, credentials, or signing keys are included.

## Host script location

`%LOCALAPPDATA%\Prometheus\Diagnostics\Prometheus-Recovery-Watchdog.ps1`

`%LOCALAPPDATA%\Prometheus\Diagnostics\Enable-TaskScheduler-Diagnostics-20261008.ps1`
