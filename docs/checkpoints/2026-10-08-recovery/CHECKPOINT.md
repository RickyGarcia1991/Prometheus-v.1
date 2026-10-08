# Prometheus recovery checkpoint — 2026-10-08

Scope: copies of internal-drive operational scripts and the earlier diagnostic report. This checkpoint does not modify the active host scripts, the removable SSD release, or existing unrelated worktree changes.

## Verified today
- SSD volume D: identified as Prometheus-2TB; Windows volume health Healthy.
- Elevated maintenance stopped lingering controller processes after eject/reconnect.
- Volume Watcher and Host Agent patched to respect controller-intentional-close.flag; both syntax-checked, watcher restart logged arrival-suppressed-intentional-close.
- Controller launched normally and showed a responsive window; graceful close returned zero processes; intentional-close flag restored and remained effective.
- Recovery supervisor started from scheduled task, launched responsive controller PID 9844, then after forced termination recovered with responsive PID 8036; log recorded both starts.
- Intentional-close flag restored after recovery test; controller remained stopped while supervisor ran.
- Original orchestrated Windows eject was accepted, and volume removal logged; that test preceded latest script patches.

## Limitations and follow-up
- The current physical safe-eject/reconnect cycle after the latest patches is NOT TESTED yet.
- Scheduled task for evidence recorder was observed Ready and manually restarted Running; persistence across future logon/reboot NOT TESTED.
- Crash-loop throttling was assessed in an earlier report; not repeated in this checkpoint.
- The checkpoint copies host-local scripts into docs/checkpoints/2026-10-08-recovery. These are snapshots, not automatic deployment sources.
- No credentials or private signing keys included.

## Working tree isolation
Pre-existing uncommitted changes in README.md, pyproject.toml, CLI, research, WPF controller, startup command, research config/docs/modules/tests are intentionally excluded from this checkpoint commit.

## Safe-eject procedure
Verify correct SSD identity and recorder/supervisor tasks. Run orchestrator only if no SSD-dependent work is active. Wait for explicit Windows safe-to-remove confirmation before physically disconnecting. User performs physical disconnect/reconnect. Verify drive return, controller intentional-close behavior, and Desktop Commander recovery from C: logs.
