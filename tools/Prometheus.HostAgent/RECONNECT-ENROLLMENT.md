# Create-only reconnect enrollment

`ReconnectEnrollment.psm1` exports `Initialize-ReconnectAuthorization`, `Start-ReconnectEnrollment` and `Repair-ReconnectEnrollment`. The exported path always uses the production verifier/provider and current user's LocalAppData/Prometheus root. Internal provider injection exists solely for module-scope tests; no CLI trust bypass is exposed.

This implements enrollment, not activation: the one reconnect task is registered **disabled**, with current-user interactive logon, IgnoreNew, unlimited duration and the existing battery/start-when-available settings. No task is started or enabled. No optional backup, communication, watchdog or legacy watcher schedule is created. Every lifecycle-name collision refuses the create-only transaction, regardless of presumed ownership; all unrelated tasks remain unchanged.

Start requires existing valid machine-bound authorization. Initialize creates it only in an elevated current-user session after signed-package validation and a second validation under the shared locks. It never invokes UAC itself. Current-user profile ownership is checked; run with normal Windows device approval. Existing valid authorization is preserved. Any other state, backup/checksum, prior transaction history or partial identity refuses fresh provisioning. The old installer is not used.

## Signed package contract

A dedicated flat enrollment bundle must contain exactly these ten files, with no directories/unlisted extras:

- Prometheus-QuietHost-v3.exe
- Prometheus-USB-Reconnect-Supervisor.ps1
- Prometheus-Lifecycle.ps1
- Prometheus-Arrival.ps1
- Prometheus-Host-Agent.ps1
- ReconnectEnrollment.psm1
- RECONNECT-ENROLLMENT.json
- SHA256-MANIFEST.json
- SHA256-MANIFEST.sig
- MANIFEST-PUBLIC.cer

The signed manifest is an array containing exactly the seven runtime/module/policy entries. Each object has exactly path (string), sha256 (lowercase 64-hex string) and size_bytes (positive integer, maximum 16 MiB). Combined payload size is capped at 32 MiB. Existing trusted signer thumbprints are preserved. Signature verification precedes payload/staging operations; both the import-time module hash and current file hash must match its signed entry; input reads are bounded, paths must not traverse reparse points, and targets are verified again before and after copying.

RECONNECT-ENROLLMENT.json has exactly:

```json
{"schema":1,"mode":"reconnect","quiet_host":"Prometheus-QuietHost-v3.exe","external_runner_sha256":"<reviewed lowercase SHA256>"}
```

The fixed external DesktopCommanderStartup/Run-DesktopCommander.ps1 is preferred. Only confirmed absence selects DesktopCommander/Start-AtSignIn.ps1; unreadable legacy state refuses fallback. The selected runner must match the signed hash. It is never copied, stopped or rewritten. Its active process does not block enrollment. Active processes referencing the target HostRoot do block enrollment; unreadable process/pause state also refuses. The signed Lifecycle candidate must already implement the reviewed root-v3 resume contract; this module does not rewrite source bytes or infer that compatibility from a filename.

## Transaction and recovery

All package, host state, task names and existing target bytes are checked before writes, then rechecked while holding the enrollment mutex and all existing drive-eject mutexes. Existing matching files remain untouched; different files refuse and require the separate forward-recovery package. New files are staged/read back and created without overwrite. The journal records absent task baseline and file intents before mutation.

Failure removes only completed, hash-matching new files and the exact disabled task carrying this transaction's description, SID, action, trigger and settings. An uncertain partial copy or foreign replacement produces rollback-incomplete and preserves dependencies/journal for explicit recovery. There is no cleanup of private journals or staging evidence. Repair-ReconnectEnrollment -Package <directory> -TransactionId <32 lowercase hex characters> performs explicit interrupted-transaction rollback. It verifies the signed package and loaded module again, derives all target paths from the fixed closure, validates exact journal fields and hashes against that package, and rechecks the disabled task and files under the same locks. A durable completion record is required for each removable file; an intent-only file is ambiguous and preserved. Changed or foreign task/files refuse before removal. Completed enrollment is preserved. No enable/start step is included.

Run `pytest tests/test_reconnect_enrollment.py tests/test_quiet_host_enrollment_plan.py tests/test_host_agent.py` using the configured development runtime. Tests inject module-internal providers or stub task/process queries, use temporary files and never call live task APIs or install anything. A real trusted signed bundle, independent security review, current-user Windows elevation and task serialization, and controlled disabled-task acceptance remain integration gates.

## Authorization interruption behavior

Identity primary and checksum are exclusive creations with Flush(true), and the authorization journal binds their hash, current-machine fingerprint and package identity before writes. Journals are also flushed before atomic replacement. A crash leaving a half-pair refuses fresh initialization. The elevated Repair command can complete an exact authenticated authorization intent: it validates the sealed identity bytes and current fingerprint, requires no protected state or other history, preserves every existing exact counterpart, and exclusively creates only the missing counterpart. Changed, partial or unrelated artifacts are refused without overwrite. Completed authorization is never reset. A pending journal temporary file is preserved and may require explicit review before retry. A successfully completed identity remains preserved even if later enrollment fails.

The focused tests cover actual temporary filesystem journal replacement, authorization pairs and half-pair faults, completed-copy rollback/idempotence, signed-identity mismatch, arbitrary path rejection, intent-only/changed files, foreign task refusal and task-removal failure. Tests do not prove live Task Scheduler acceptance or invoke Windows elevation.


## Authenticated recovery authority

Journal files contain a bounded DPAPI CurrentUser envelope, with entropy binding the transaction ID and signed package identity. Recovery authenticates/decrypts it before using phase or ownership records. Plaintext, forged, modified and transplanted envelopes are rejected; no plaintext compatibility fallback exists. This is an integrity boundary against altered stored artifacts, not protection against malicious code already executing as the same Windows user. The trusted caller must verify the module before importing it; self-verification cannot secure a malicious bootstrap.

Authorization recovery additionally requires elevation/current-user profile verification and the current machine fingerprint. Neither-primary-nor-checksum, primary-only and checksum-only states are recoverable only for an authenticated pending intent and otherwise empty protected-state baseline. Task/file recovery never automatically resumes execution.

## Existing profile compatibility

Fresh authorization and authorization recovery preserve exactly six known unrelated legacy entries: directories `Controllers` and `logs`, and files `controller-intentional-close.flag`, `memory.sqlite3`, `Prometheus-User-Watcher.ps1`, `user-watcher.log`. This allowance neither executes nor modifies their contents. Types must match. Directory metadata inspection refuses reparse points, unreadable entries, more than 4096 descendants or more than 16 nested directory levels. Unknown root entries, security state/checksums/backups, runtime dependencies and other enrollment histories still refuse. Existing active owned processes continue to block enrollment independently.

Source contracts identify Controllers in HostAgent/Drive Watcher, logs in wait-ollama.ps1, and the intentional-close flag in DriveController. The remaining three exact names are the user profile baseline supplied by the parent preflight; they are preserved as data, not trusted runtime dependencies.

## Task account canonicalization

Task ownership resolves both principal and logon-trigger accounts to Windows SIDs before comparison. Valid SID literals and the equivalent local or qualified Windows account name identify the same owner. Different SIDs, malformed literals and resolution failures refuse ownership. All action, disabled-state, transaction-description, trigger and settings checks remain required. This permits Windows Task Scheduler's canonical account-name serialization without accepting a different user.
