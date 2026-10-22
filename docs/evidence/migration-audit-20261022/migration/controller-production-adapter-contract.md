# Bounded production adapter contract

Installed baseline remains R5. R7 is a signed candidate. This document defines implementation gates, not permission to activate or evidence of activation.

## Boundary with the separate runtime migration

The migration owner replaces only the exact signed Host-Agent file while preserving the enrolled task identity/configuration, authorization and independently launched applications. Pointer activation must not proceed until that migration has an independently verified completion receipt, exact installed R7 file hashes and restored intended task state. Neither operation may interpret the other's receipt as controller pointer or launch acceptance.

## Physical identity and custody

Run outside MSIX in the pinned registry profile/current SID and MachineGuid binding. Hold a real profile-root handle and verify final resolved path; refuse reparse ancestors/targets. Bind SSD by exact label, volume size, volume unique identity and physical disk identity, not drive letter alone. Revalidate before every durable boundary. Open metadata from that verified volume only. Legacy drive prefixes are hints only; preserve the exact old bytes/hash and never follow the stored drive.

The adapter must pin its code, core, signature verifier, package identities and certificate policy. Keep candidate and strictly newer distinct recovery closures immutable throughout verification/staging. Enforce bounded safe manifest entries, no unexpected executable files, no reparse or path escape, and exact signature/provenance/version binding. Source must exist even when cache is already valid.

## Actual lock and process order

Existing enrollment acquires Local\\PrometheusReconnectEnrollment then Local\\PrometheusEject-A through -Z, nonblocking. Preserve this order. Supervisor holds Local\\PrometheusUsbLifecycle for its lifetime, so do not wait for it before stopping the verified owned wrapper: that would deadlock or always refuse.

Under enrollment/eject exclusion, authenticate intent, recheck exact task configuration and captured PID/creation/path/current-user ownership, disable that exact task, and stop only its captured wrapper using the separately qualified immediate-child job containment. Independently launched app/controller/DC processes must survive. Then acquire UsbLifecycle, PrometheusArrival and PrometheusHostAgent nonblocking to exclude both supervisor and independently invoked workers. Busy/ambiguous state refuses mutation and records how to restore the original task state. Check pause/eject markers with fail-closed reads; a snapshot alone is not exclusion.

These mutexes are Local and therefore session-scoped. Verify execution/session compatibility and refuse competing same-user workers in another session; do not claim machine-wide exclusion. Hold runtime exclusion through cache/floor/pointer/receipt operations; release it before restoring/restarting the supervisor, otherwise its child cannot acquire its lifetime mutex. Enrollment/eject exclusion remains until final ownership/state readback. Recover abandoned mutexes only with fresh full state validation.

## Provider method obligations

- `exclusive`: the above real lifecycle protection, not the fixture's private lock file.
- `verify`: pure signed identity validation, independent of admission floor, with immutable exact manifest bytes and payloads. Normal production `Get-Package` retains admission checks. Below-floor identity inspection is not launch authorization.
- `stage`: separate create/stage/verify/publish cache operation with no floor changes; **never call Sync-Controller**, which advances floor. Preserve existing cache/recovery material. Replay verifies already published exact bytes rather than replacing unrelated content.
- `read_floor` / `set_floor`: actual protected-state logic with monotonic recovery. `Get-SecurityState` may repair primary from backup, so even a read requires the write-capable exclusion boundary. Never substitute or reset a mocked floor to make verification pass.
- `write_intent`: immutable authenticated bounded transaction record binding host, volume, original metadata bytes, signed candidate/recovery identities and original task state. Use protected CurrentUser DPAPI key custody outside editable journals with restrictive ownership; detect missing/corrupt key rather than regenerate it over existing history. No raw keys/authorization in public evidence.
- `replace_pointer`: exact expected-byte comparison under the same exclusion, flushed sibling staging and atomic same-volume replacement, readback and retained original evidence. Reject changed unrelated metadata; never overwrite it from a stale plan. Do not lower the durable floor on failure.
- `write_receipt`: immutable per transaction and target manifest, preserving both candidate and forward-recovery completion. Replay may repeat idempotent verification/staging/floor operations; it is not a no-op claim. A successful pointer write followed by receipt failure must resume without repeating launch or destructive cleanup.

## Focused qualification matrix

| Boundary | Required adverse case | Required result |
|---|---|---|
| Identity | Wrong SID/profile/MSIX, remounted drive, reparse, changed disk ID | Refuse before state mutation |
| Exclusion | Live supervisor, independent Arrival/HostAgent, competing session, held eject lock | Bounded refusal or exact owned pause; unrelated applications survive |
| Package | Missing source, coherent unsigned replacement, changed manifest/provenance, staged corruption | Refuse before cache/floor/pointer publication |
| Journal | Tampered/replayed/transplanted intent, wrong DPAPI context, lost key | Refuse before authority-bearing mutation; no silent reset |
| Metadata | Legacy drive prefix, nested unrelated fields, concurrent non-controller edit | Resolve under verified SSD only; preserve fields or refuse CAS |
| Durability | Crash after cache, recovery-floor slot, primary floor, pointer, before receipt | Fresh-process resume with monotonic floor and exact pointer/cache agreement |
| Recovery | Candidate below actual floor; recovery below floor; newer independent commit | Never restore older pointer as success; explicit forward recovery or conflict |
| Completion | Retry candidate then forward recovery then retry | Two immutable receipts, unchanged authorization, no implicit launch |
| Task restore | Failure after disable/stop, start denied, XML changed | Explicit partial state and bounded recovery; never recreate or overwrite foreign task |

Use generated normal-profile directories and independently generated test tasks/processes first. Real physical-drive pointer publication remains a separate qualified run. Physical unplug/reinsert, reboot and protected-interface acceptance must remain explicitly untested until performed.

## Why the temporary provider is not production-ready

Its synthetic host/volume assertions, private lock file, generated DPAPI root/key, mutable fixture plan, package copies and modeled ownership do not bind the active R5 installation. It does not pause the real task or exclude Arrival/HostAgent, preserve enrolled task state, provide machine/session concurrency proof, or implement a production signed cache publication policy. Actual signature/floor/DPAPI tests validate primitives, not those integration obligations.

## Minimal implementation sequence

1. Complete isolated migration-core review and exact task/file recovery tests; keep live R5 unchanged meanwhile.
2. Implement native adapter identity/lock/journal primitives and test refusal paths without pointer mutation.
3. Implement no-floor signed cache staging and atomic metadata CAS under those primitives; run complete generated fault matrix across fresh processes.
4. Review frozen adapter and signed inputs, then perform separately recorded bounded migration and pointer activation with verified backup and forward recovery available. Launch/readiness remains a separate final operation.
