# Prometheus recovery validation — 2026-10-08

## Results
- Signed controller trust: PASS (previous verification).
- Controlled single-crash recovery: PASS (previous verification).
- Controller crash-loop recovery: PASS, live test 2026-10-08 14:08 local.
- Restart rate limiting: PASS; 3 observed controller restarts, 4th blocked; cooldown state set.
- Intentional-close restoration: PASS; flag present and controller process count 0 after test.
- Internal-drive recovery recorder: PASS; scheduled task running and JSONL baseline observed.
- Physical SSD disconnect/reconnect: NOT TESTED — requires user handling hardware.
- Windows volume event / real USB fault cause: NOT TESTED — no physical disconnect performed.
- Cooldown expiry and subsequent restart: NOT TESTED — not waited through five minutes.
- Long-term recorder resilience / log retention: NOT TESTED.

## Evidence
- crash-loop-test-20261008.json — three restart PIDs and cooldown test results.
- recovery-observations.jsonl — internal-drive timestamped state transitions.
- usb-supervisor.log — supervisor messages.
- controller-restart-state.json — transient state (restored/removed after test).
- Prometheus-Recovery-Recorder.ps1 — recorder source.
- Run-Controller-Crash-Loop-Test.ps1 — controlled test source.

## Recorder behavior
Scheduled task: Prometheus Recovery Evidence Recorder; starts at user logon.
Writes only when sampled state changes, except retry-state observations.
Sampling interval approximately 2 seconds. Short-lived processes/events can be missed.
Recorder currently records volume and process states, not a complete causal Windows event trace.
Recorder is on internal C: drive, independent of removable D: SSD.

## Physical test — user action required
1. Save all open work and close applications that are actively writing to D:.
2. Confirm the recorder task is Running and note the current time.
3. Keep the computer on, leave the recorder running, and keep Prometheus controller intentionally closed for the initial test.
4. Only when safe, physically disconnect the external SSD; do NOT change the drive letter or force Windows disk removal.
5. Wait 15–30 seconds, reconnect the SSD, and wait until Windows recognizes the correct volume.
6. Return to ChatGPT and report the approximate removal/reconnection times and any errors.
7. The assistant can then inspect the JSONL and supervisor logs, assess the Windows volume transition and determine PASS/FAIL/BLOCKED.
If Windows warns that the drive is busy, or a write operation is active, stop rather than disconnecting it.
Do not attempt this if the remote-control connection or active workloads depend on the SSD.

## Limitations
This physical test does not yet prove automatic controller restart after SSD return because the intentional-close flag remains active. A separate explicitly authorized desired-running recovery test would be needed.
Do not mark hardware recovery PASS from software simulations alone.
