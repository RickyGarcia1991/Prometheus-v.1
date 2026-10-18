# Automatic placement and approved waiting — development checkpoint

Updated 2026-10-15T17:02:06.362575+00:00. Implementation and Dell validation are complete for bounded Python syntax jobs. The explicitly approved isolated native Surface scheduler test passed: signed resource admission, durable reopening, one execution and original signed-result recovery. Revision 2 packaging is in progress after the revision 1 timeout cleanup failure; installed dev26 revision 1 and production Surface enrollment remain active.

## Implemented behavior

Prometheus compares the local computer and its enrolled Surface before asking for approval. Ready computers take priority, then available RAM headroom after reserves and the compiler estimate, then CPU headroom. Readings must be fresh, with confirmed external power and sufficient disk/RAM capacity. The initial 256 MiB compiler allowance uses a 1.5 uncertainty margin; this is a conservative baseline, not learned prediction for every workload.

The approval screen shows the exact destination/account/worker, source snapshot and hashes, selection explanation and up to one hour of waiting permission. Approval fixes those files and that destination. A newly busier destination waits instead of silently moving the job elsewhere. Changed policy, identity, cancellation or expired authority prevents a new execution.

The owner-local SQLite waiting store saves approved source and metadata before execution. Waiting survives app restarts. Opening the queue after the same owner unlocks arms the existing idle loop; it checks at most one due job about every 30 seconds and gives active foreground work priority. Locked/closed apps do not run waiting work. Time rollback expires pending authority. The bounded store retains at most 100 jobs and 16 MiB, with 64 KiB per record; capacity exhaustion reports an error rather than discarding unresolved records.

Atomic claims ensure competing processes cannot start the same job twice. A claim interrupted before recording completion becomes unconfirmed and cannot be automatically replayed. Surface recovery queries the original signed result. A local claim without a saved result stays unconfirmed; a new check needs a new exact review. The UI shows waiting reasons/deadline, cancellation and recovery controls. It refreshes saved state every 30 seconds while visible and idle and clears details on lock.

The local compiler now enforces Windows Job Object CPU, memory and process limits before reading source, with a parent wall deadline. Both workers compile source without executing it or modifying project files.

## Verification

- Complete current-source regression: **3,001 passed, eight documented skips**, zero failures/errors; 337.58 seconds.
- **All 28 browser-interface suites passed** after the final immediate-refresh fix.
- Real Interface approval/restart/idle-resume coverage uses the real approval and SQLite layers with substituted destination transport. The 38 focused tests are part of the full count, not additional tests.
- Native Dell qualification passed after the actual 30-second wait: real admission, once-only bounded compilation, original bytes after reopening, deliberate invalid syntax detected, no source execution marker and no repeated execution after completion.
- Independent review found and verified fixes for owner mismatch and resource freshness around sampling, authorization and claiming. No unresolved actionable finding remained in that review.
- Browser component checks passed for exact review, waiting display, reload, cancellation, query-only recovery controls and lock. Those visual checks used simulated resource/API outcomes; they do not claim installed end-to-end acceptance. Screenshot: Prometheus-scheduled-waiting.png.
- Eight skips are itemized in the validation JSON: six Windows symlink privilege cases, the absent offline migration preparer and the Linux-native compiler case. A separate native Windows junction rejection test passed. The Linux-native worker was separately qualified on the actual Surface.

## Remaining qualification and installation

The owner explicitly approved the exact 73,880-byte Surface test bundle. Transfer and SHA256 verification succeeded (991b8d8d433374e20725ddab5daa3e89595a3117881d72f0caa3ac76c3e0cc33). The native test passed in 31.635 seconds, including the actual 30-second wait, fresh authenticated resources, once-only bounded compilation, signed original-result query and unchanged worker ledger. The deliberate syntax error was detected, denial stayed inert, no source execution marker appeared and the temporary fixture was removed. The initial transfer rejection is retained as historical evidence; the approval requirement is resolved.

Corrected revision 2 qualification, full app-to-Surface acceptance and separately approved app/worker installation remain required. The installed worker needs the new resource-query protocol for automatic remote placement. This feature covers syntax checks; broader job types, learned workload calibration, independent provider review, GPU reliability, physical disk pressure, battery-drain measurement and the SSD memory/vision/retrieval plans remain separate unfinished work.

## Evidence

Prometheus-scheduled-validation.json records exact counts, skip reasons, native evidence, browser scope and hashes of 15 implementation/test files. Full logs: Prometheus-scheduled-final-regression.log/.xml and Prometheus-scheduled-final-ui-tests.json. Native/local result: Prometheus-scheduled-native-local.json. Native Surface result: Prometheus-scheduled-native-surface.json. The initial component fixture's inline-script escaping error was corrected in the test fixture; the browser-discovered immediate-refresh race was corrected in product JavaScript and has a regression test.

Both loopback progress services have been verified responding with the current milestone. The Dell status server uses about 16.6 MiB excluding the browser. Physical Surface-window visibility was not visually checked. Timestamps indicate recorded milestones, not continued development between chat turns.

## Windows timeout repair — revision 2

The revision 1 packaged run exposed a native timeout cleanup race: the virtual-environment redirector could exit before the actual interpreter had finished exiting, briefly retaining its temporary working directory. A bounded native probe confirmed the different PIDs and surviving interpreter. The regression now asserts that the directly owned process is the compiler, its termination is observed, and the temporary directory is removed. It failed against the redirector implementation before the fix.

The Windows syntax worker now launches the base interpreter directly with isolated standard-library startup. Its exact launch path and contract are included in the worker identity so pending reviews cannot silently adopt the changed execution mechanism. Source is still compiled without execution, and existing CPU/memory/process limits remain. The focused compiler, approval and durable waiting/recovery run passed 155 tests with one documented symlink-privilege skip. Full corrected-package qualification remains required.
