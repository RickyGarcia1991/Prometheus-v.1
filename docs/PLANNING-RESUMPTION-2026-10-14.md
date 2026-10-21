# Prometheus: resumed work from the SSD plans

Owner direction: use the existing Prometheus plans and prioritize unfinished work.
The Surface is available for additional checks when useful. Keep one authoritative
development checkout and use the Windows host for Windows release acceptance.

## Current baseline

The installed portable release is 0.8.0.dev24. Phase-one conversational editing,
approved task delegation, checks, persistent undo and idle public research have
already been delivered. Installation and startup were verified on October 14.
Dev24 adds combined exact approval, durable grouped undo and promotion health
compatibility. Its 169-file backup/restore, all three signed copies, six health
checks and startup passed on October 14 at 10:02 PM America/New_York.
The signed release is separate from the new automatic-selection and recovery
development changes described below.

## Planning records reviewed

| Record in the active SSD release | How it informs current work |
| --- | --- |
| `docs/MASTER_SPEC.md` | Correctness, reversible changes, durable knowledge and tested releases remain the engineering priorities. |
| `docs/INTEGRATION_MILESTONES_2026-10-08.md` | Recovery, memory quality, model evidence and reviewed self-improvement remain acceptance areas. Its original research-provider status is historical. |
| `docs/ai/planning/2026-10-03-feature-offline-chat.md` | All listed foundation/offline-chat tasks are already checked complete; do not rebuild them. |
| `docs/PROJECT-TASKS-PHASE1-2026-10-14.md` | Establishes the delivered one-file task workflow and the separation of later multi-file, repair and orchestration work. |
| `docs/APPROVED-INTEGRATIONS-2026-10-13.md` | AgentMemory and public-page Browser Use operations exist; full browser-agent and wider integration acceptance are still distinct. |
| `docs/STATUS.md` | The original dev1 checkpoint is historical; use this index for resumed work. |

## Prioritized work queue

1. **Completed in development: promotion health compatibility.** The health
   producer omits zero counts. Promotion rejected its valid six-pass report.
   The consumer now accepts omitted zero counts, checks that counters agree with
   individual results, and requires all six installation checks to pass. Failed,
   blocked, missing, duplicate or malformed evidence cannot qualify a release.
   This change is installed in dev24.
2. **Next implementation milestone: multi-file project tasks.** Extend the
   phase-one task through review, bounded file selection, exact diffs, apply,
   checks and undo as one coherent change set. Before any write, validate all
   targets and their baseline hashes. Acceptance must include two-file success,
   one stale target, a partial apply failure, check failure, cancellation and a
   concurrent newer edit during recovery. Preserve the existing one-file flow.
   Execution foundation now implemented in `project_change_set.py`: up to eight
   reviewed candidates, complete preflight, checks after all writes, deduplicated
   check plans, reverse recovery and durable individual undo records. Fresh
   editor/workspace regression: 107 passed, one Windows symlink privilege skip.
   Real unittest cases verify checks observe both edited files and failed checks
   recover both original files. Explicit multi-file generation, combined approval
   and durable group undo are now connected. Fresh expanded verification: 150
   passed, one Windows symlink privilege skip; three JavaScript suites passed.
   Actual authenticated browser acceptance with Qwen3 passed denial, approval,
   both compile checks, reload/reopen and exact-original grouped undo. Automatic
   coordinated selection and crash-resume remained separate from installed dev24.

   Current development: optional automatic selection from a bounded actual file
   catalog feeds exact combined review. A durable journal precedes project-task
   writes. After process interruption, opening the project offers explicit
   restoration of original snapshots; newer edits block restoration. Unknown
   tests are not rerun, expired approvals are not replayed. This recovery milestone
   does not implement unattended forward resumption or boot-time task scheduling.
3. **Then: practical application self-improvement.** Carry a reviewed multi-file
   candidate through real checks, signed packaging, installation, restart and
   rollback. Deployment continues to use the owner's separately reviewed release
   approval; development authorization is not an installation receipt.
4. **Operational follow-up before extended unattended runs:** investigate the
   recorded USB reset/delayed-write incident. All release and supported data
   hashes passed after recovery, but that does not establish the hardware cause.
   Physical reconnect/other-computer acceptance remains an explicit device test.
5. **Later: restart/resume and unattended repair.** Make interrupted jobs and
   recovery state durable. Desktop Commander's Windows sign-in startup has been
   configured separately; that does not establish Prometheus job-resume behavior.
6. **Later: memory and model quality.** Add realistic contradiction/relevance
   evaluation and a fixed model task set with latency, memory and answer quality.
   Existing Qwen3 editing/research examples are evidence for those cases only.
7. **Later: broader agents/providers.** Qualify the remaining Browser Use,
   OpenViking and OpenCode workflows. Catalogue presence or native imports alone
   do not count as completed end-to-end operation.

## First repair: fresh evidence

- The new producer-to-consumer regression reproduced the original defect:
  sparse counts failed, while explicit zero counts passed.
- Final affected suite: **171 passed in 38.01 seconds**, covering health,
  promotion/rollback, self-improvement and maintenance.
- The fixed development consumer accepted a fresh read-only health report from
  the actual installed dev23 release: **6 passed, 2 explicitly untested**.
- The six required checks are release metadata, code checksums, memory database,
  runtime presence, model-file completeness and RAM feasibility. Passive physical
  acceptance and inference are not silently promoted to passed.
- No signing keys, device credentials, installed release files or live selection
  were changed by this development repair.

Next release preparation must include fresh integrated verification for its final
scope. The current result establishes this focused repair, not completion of all
remaining phases or a newly installed release.

## Automatic selection and recovery: browser acceptance

Authenticated development-preview acceptance passed automatic two-file selection,
exact combined approval, both syntax checks, and byte-exact grouped undo. A real
interrupted-write journal was discovered on project open; browser restoration
returned both CRLF originals and re-enabled task preparation. Focused regression:
142 passed, 1 Windows symlink privilege skip; all 24 JavaScript UI suites passed.

First browser apply encountered a transient portable Python startup stall; its
122-second timeout rolled both files back correctly. An independent startup probe
also timed out, then a later probe and repeated browser apply passed. Cause remains
unresolved; retain this as an operational follow-up before unattended runs.

These changes remain development-only. Full final-candidate regression, packaged
verification, and separate owner installation approval remain before deployment.

## Resource admission and verification connected

Project-task approval now includes resource estimates and pending exact-snapshot evidence. Execution forces fresh resource readings before writes and releases reservations on exit. Actual file hashes and runner results feed the verification council; reporting failure preserves the real edit and undo record. Activity shows cached native RAM, CPU and power readings.

Validation: full regression 2346 passed / 4 skips; final affected suites 90 passed; UI 82 passed; authentication module separately passed 12 tests using existing packaged dependencies. Live qwen3:1.7b two-file proposal, exact approval, both syntax checks, bound verification and byte-exact grouped undo passed. Fixed stale workspace error after a successful retry; all UI tests passed again.

Operational follow-ups: preview launcher lacked runtime environment and was corrected. GPU discovery stalled; temporary process-only CPU selection restored readiness. Surface remote connector remains offline despite user reconnect; primary monitor updated, Surface update not confirmed. No installation performed. Resource estimates remain uncalibrated admission estimates; measured workload collection and remote dispatch remain future work.

## Measured workload history and restored Surface connection

Native Windows direct-test-process counters now feed bounded host/check-family history. Successful complete checks calibrate estimates; cancelled, timed-out, failed or incomplete records do not. Runtime startup derives the executable and model paths from the selected portable root. UI results expose actual counters and history-write status without losing undo on reporting failures.

Validation: full regression 2371 passed / 4 skips; final calibration correction 65 affected tests passed; all 82 UI tests passed followed by two final affected suites for review status text. Live preview passed actual edit/check verification, recorded native counters, exact-byte undo, subsequent forecast using one saved sample, and denial of the second test-only proposal. Browser receipts are retained in the task outputs.

Desktop Commander primary connection restarted using its existing sign-in launcher. Surface reconnected with a new registration; its monitor was restarted, progress written and its status response verified. Monitor server used about 20.3 MiB RAM. SSD queue updated with prior queue preserved. Installed dev24 unchanged; packaged release verification and separate installation approval remain outstanding.

## Signed resource-verification candidate and next startup slice — October 15

Frozen dev25 is prepared and signed: 847 verified payload files, exact archive,
2,387 packaged regression tests passed with three documented skips, 82 packaged
UI tests passed, real portable-Python authentication/application imports and native
process counters passed. All 12 packaged authentication tests ran. Read-only
installation preflight verified candidate, current dev24 selection, pinned tools
and fresh recovery destinations. Separate owner installation approval is pending.

The initial full package run exposed a missing Windows multiprocessing main guard
in the external test launcher. Correcting that launcher yielded 38 affected tests
and then the full clean run above; the signed application was unchanged.

Later source development, excluded from frozen dev25, prefers the packaged startup
helper and adds server/runner/ambiguous identity classification, owned process
tracking and bounded whitelisted startup phases. Independent combined helper,
startup and request-timing validation: 44 passed. No live runtime was started,
stopped or reconfigured for this slice. GPU backend/driver repair, backend choice
and controlled recovery remain outstanding. Installed dev24 is unchanged.

## Current checkpoint — dev25 installed; next development qualified

The preceding sections are historical. Dev25 installation was separately approved
and completed with a verified 169-file backup, isolated restore, signed recovery
copies and successful supported startup. Dev24 remains available for rollback.

Subsequent development now includes owner-reviewed runtime backend preferences,
actual explicit-CPU startup/model qualification, job-associated Windows counters,
Linux battery/AC observations and a signed bounded Surface syntax worker with a
durable once-only ledger. Native Surface signature, compiler, admission and crash
recovery acceptance passed. The new worker is not installed for automatic dispatch.

See [Runtime and Surface development](RUNTIME-AND-SURFACE-DEVELOPMENT-2026-10-15.md)
for exact evidence and remaining integration dependencies. Both progress feeds
carry these milestones. Live preview acceptance awaits normal owner unlock.
