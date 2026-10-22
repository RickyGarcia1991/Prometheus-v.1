# Active R5 to R7 migration preparation

Development-only state machine and injected fault tests. No native adapter or activation command exists and nothing was executed against live state.

Only Prometheus-Host-Agent.ps1 may change. The immutable authenticated plan binds exact R5/R7 manifest identities, old/new file hashes, host/profile, authorization hash, existing task identity/configuration hash and original enabled/running state, plus exact wrapper/child captured identities. The adapter must derive these from trusted signed inputs and physical observations; journal text cannot supply authority by itself. Existing task identity/settings, authorization, user data, application/controller and old rollback evidence remain preserved. This is not the old disabled R3 create-only migration.

Lock design from actual code: acquire Local\PrometheusReconnectEnrollment then Local\PrometheusEject-A through Z nonblocking; release in reverse. The running supervisor holds Local\PrometheusUsbLifecycle throughout its lifetime, so that lock cannot be acquired before stopping it. With exact task ownership verified, durably record disable intent, disable only that task, then stop the captured wrapper only. R5 job flags KILL_ON_JOB_CLOSE plus SILENT_BREAKAWAY_OK terminate its immediate supervisor while independently launched application/controller/Commander descendants survive. Verify both captured identities have exited. Acquire UsbLifecycle nonblocking, refuse separately active Arrival/HostAgent work, backup/read back exact old HostAgent, then CAS replace only old or transaction-proven new bytes. Release UsbLifecycle before restoring original enabled state and starting the same task, or the new supervisor would exit on the held singleton mutex.

Each operation's authenticated intent is flushed before mutation. Resume replays a pending owned operation idempotently. Backup is create-only/readback; replacement recognizes only exact old/new bytes with authenticated transaction provenance; ambiguous ownership/refusal preserves state. start_once must inspect exact same-task current process identity and suppress duplicate start after lost completion. Complete journal replay validates completion and performs no operation. The helper itself has no process-stop implementation and grants no arbitrary PID/path capability.

57 tests pass across before/after operation and journal boundaries, tampered journal/key/auth/task/package refusal, preservation of task/auth binding and one restart despite lost completion. Tests use an injected in-memory provider; they establish state-machine behavior, not native Task Scheduler or process safety.

Remaining gates before live execution:
- Native provider exact signed seven-entry closure comparison and physical profile/host guard; protected DPAPI key/journal storage.
- Full original task XML snapshot and byte/config ownership checks accounting only for temporary enabled flip; exact current SID.
- Native bounded wrapper stop with PID creation-time/path/hash revalidation; immediate-child disappearance proof; application/controller/Commander preservation checks.
- Quiescence refusal for independent Arrival/HostAgent, lifecycle/eject synchronization, native backup/CAS and durable fault tests in temporary fixtures.
- Same-task restart proof against actual Task Scheduler IgnoreNew semantics and interrupted completion; no broad process killing or task recreation.
- Explicit recovery operator contract when signed state differs: preserve/quarantine evidence, never silently restore unowned state.

This core does not make the active R5 migration production-ready. R7 and R5 package pins remain exact, and the installed system is unchanged.

Independent lock review refinement: after captured pair stop, hold Local\PrometheusUsbLifecycle, then Local\PrometheusArrival, then Local\PrometheusHostAgent nonblocking across replacement. A process snapshot alone cannot exclude Arrival/HostAgent. Release these runtime locks before restart. Because Local mutexes are session scoped, the native provider must bind the session and refuse competing same-user workers in other sessions; a scheduled process cannot assume it shares Codex's session. This is a required native gate, not implemented proof.


The completed-chain tests use a model provider: they check read-only sequencing and injected refusal, not original receipt signature authentication, seven-entry signed closure comparison, or installed filesystem identity. Those remain native provider and integration gates.

