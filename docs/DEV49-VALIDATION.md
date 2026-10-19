# Dev49 revision 2: historical evidence lifecycle and conflict handling

Contradictory valid reports for one native execution previously made the owner's whole history unavailable. This change keeps unrelated decisions visible and marks only the exact same-owner execution family as conflicting. All original observations stay available; no result is selected as authoritative.

Read-only lifecycle states distinguish missing execution, pending or unconfirmed outcomes, recorded results, restoration and conflict. Exact project-result references can be shared without spreading one decision's Undo to another. Raw validation, owner isolation and approval/execution boundaries remain enforced. History rendering reuses one canonical ledger snapshot, including off-page conflicts.

## Qualification

- 4,442 complete packaged tests passed; 14 documented platform/tool-payload skips and one existing-host identity exclusion.
- All 33 packaged UI suites passed.
- 97 isolated tests passed on the Ubuntu Surface, with verified source/dependencies and local evidence readback.
- 96 isolated installer/recovery checks passed; independent final review passed.
- Normal passkey-authenticated preview checks passed for nine explicitly labeled generated history records, three conflict observations, unrelated success, original project result, row-local restoration, missing evidence, dashboard counts and browser reload.
- Exact canonical generated receipts/events were unchanged after the browser checks. No project, worker, settings or Undo execution occurred in these read-only examples.
- Portable Python startup, signature/tree/archive integrity, retained baseline/source integrity, installation preflight and a fresh isolated-copy saved-history snapshot passed.

The original candidate's full run had one failed redundant-ledger-read assertion (4,434 passes, 14 skips, one exclusion). Its package/results remain retained. Revision 2 fixes the extra read and adds seven focused pagination/off-page tests; the correction passed independent review and 110 focused tests with one platform skip before this clean full run.

## Candidate identity and rollout

Version: 0.8.0.dev49 revision 2; 1,007 signed payload files.

Manifest SHA-256: `533483c7981712bb27c504bbdaca651ee2847fe3ff0d9f201c3c8cf99dd136eb`.
Archive SHA-256: `837002601d8e3689557dabdd416ae0cecbf3457ac76945a62485497e70bc2937`.

Production installation is separately recorded. Dev48 revision 2 remains the rollback target; the reviewed transaction requires a fresh verified separate-volume backup and isolated restore. Worker identity, enrollment and production worker package stay unchanged.

On 2026-10-19 UTC, the first production transaction verified a fresh 184-file backup and isolated restore, verified all three signed copies, selected dev49 and passed native installed health. Startup readiness then timed out while Windows reported repeated USB-storage resets and disk retries. The cooperative rollback stop also timed out before selection restoration. The retained installation receipt remains `recovery_required`; it records that failed attempt and has not been overwritten.

After the resets stopped, a separately reviewed in-place recovery completed at 01:47:56 UTC. It verified the exact existing selection and trust, all three signed copies and retained dev48, then created a new verified 184-file backup and isolated restore. The pinned launcher reached the normal enrolled owner gate. No selection rewrite, forced process stop or live-data restore was needed. The separate successful receipt links the original failed receipt by SHA-256. Sixty-seven isolated recovery tests and 38 acceptance-adapter tests passed; the initial adapter test setup errors remain retained. The signed application package was unchanged.

All eight installed native checks passed: startup, retained job history, mode policy, measurements, decisions, device health, capability revisions and evidence lifecycle. The user authenticated normally; installed version, dashboard/history refresh, retained worker results and browser reload passed. Five saved claims were preserved without redispatch. The production UI has zero owner decision rows and three saved worker results; one retained invalid.py syntax failure remains visible alongside two successful results. This is historical evidence, not a new release-test failure. Prometheus's provider reconnection is recorded separately from these acceptance results. No new model qualification or worker enrollment is claimed.

Prometheus's provider then reconnected and its refreshed list reported the selected Surface online. The health dashboard continued to show a stale cached-availability warning; that discrepancy is retained as a follow-up and is not counted as verified fresh dashboard connectivity. No remote action was submitted during reconnection.

Before the original failure, SSD and flash checkpoints each verified 128 files. The final closeout creates a new uniquely named readback-verified checkpoint containing the original failure, successful recovery and installed acceptance. Exact paths, file counts and hashes live in its separate checkpoint receipt. Physical cable/USB reliability beyond this observed recovery is not established.

This draft source update is stacked on codex/dev48-release-sync. It does not merge older release branches. This milestone qualifies historical evidence handling, not new model accuracy or completion of the wider Prometheus roadmap. The preview's existing component-check warning and disconnected isolated provider were visible and not counted as feature passes.
