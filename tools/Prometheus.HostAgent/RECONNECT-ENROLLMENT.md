# Create-only reconnect enrollment

`ReconnectEnrollment.psm1` exports only `Start-ReconnectEnrollment -Package <directory>`. The exported path always uses the production verifier/provider and current user's LocalAppData/Prometheus root. Internal provider injection exists solely for module-scope tests; no CLI trust bypass is exposed.

This implements enrollment, not activation: the one reconnect task is registered **disabled**, with current-user interactive logon, IgnoreNew, unlimited duration and the existing battery/start-when-available settings. No task is started or enabled. No optional backup, communication, watchdog or legacy watcher schedule is created. Every lifecycle-name collision refuses the create-only transaction, regardless of presumed ownership; all unrelated tasks remain unchanged.

The module requires existing valid host authorization tied to the current machine. It does not mint identity or invoke UAC. A fresh Dell without authorized-host.json cannot enroll yet; separate reviewed authorization provisioning is required. Do not use the old nontransactional installer to bypass this prerequisite.

## Signed package contract

A dedicated flat enrollment bundle must contain exactly these nine files, with no directories/unlisted extras:

- Prometheus-QuietHost-v3.exe
- Prometheus-USB-Reconnect-Supervisor.ps1
- Prometheus-Lifecycle.ps1
- Prometheus-Arrival.ps1
- Prometheus-Host-Agent.ps1
- RECONNECT-ENROLLMENT.json
- SHA256-MANIFEST.json
- SHA256-MANIFEST.sig
- MANIFEST-PUBLIC.cer

The signed manifest is an array containing exactly the six payload/policy entries. Each object has exactly path (string), sha256 (lowercase 64-hex string) and size_bytes (positive integer, maximum 16 MiB). Combined payload size is capped at 32 MiB. Existing trusted signer thumbprints are preserved. Signature verification precedes payload/staging operations; input reads are bounded, paths must not traverse reparse points, and targets are verified again before and after copying.

RECONNECT-ENROLLMENT.json has exactly:

```json
{"schema":1,"mode":"reconnect","quiet_host":"Prometheus-QuietHost-v3.exe","external_runner_sha256":"<reviewed lowercase SHA256>"}
```

The external DesktopCommanderStartup/Run-DesktopCommander.ps1 must match that signed hash. It is never copied, stopped or rewritten. Its active process does not block enrollment. Active processes referencing the target HostRoot do block enrollment; unreadable process/pause state also refuses. The signed Lifecycle candidate must already implement the reviewed root-v3 resume contract; this module does not rewrite source bytes or infer that compatibility from a filename.

## Transaction and recovery

All package, host state, task names and existing target bytes are checked before writes, then rechecked while holding the enrollment mutex and all existing drive-eject mutexes. Existing matching files remain untouched; different files refuse and require the separate forward-recovery package. New files are staged/read back and created without overwrite. The journal records absent task baseline and file intents before mutation.

Failure removes only completed, hash-matching new files and the exact disabled task carrying this transaction's description, SID, action, trigger and settings. An uncertain partial copy or foreign replacement produces rollback-incomplete and preserves dependencies/journal for explicit recovery. There is no cleanup of private journals or staging evidence. Process termination/crash between operations requires journal-based recovery; the in-process rollback is not a claim of automatic crash recovery. No enable/start step is included.

Run `pytest tests/test_reconnect_enrollment.py tests/test_quiet_host_enrollment_plan.py tests/test_host_agent.py` using the configured development runtime. Tests inject module-internal providers or stub task/process queries, use temporary files and never call live task APIs or install anything. A real trusted signed bundle, current-user Windows task serialization, fresh authorization provisioning and controlled disabled-task acceptance remain integration gates.
