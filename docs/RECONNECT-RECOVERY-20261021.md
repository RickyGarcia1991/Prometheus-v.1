# Reconnect enrollment and recovery checkpoint

Development checkpoint; installed companions remain unchanged. Routine dual-display updates are disabled at user request.

The new create-only enrollment module validates a signed fixed closure, stages bytes, journals changes and registers only a disabled reconnect task. Existing task collisions refuse; rollback preserves changed or uncertain ownership. The unchanged external communication runner is verified and remains running. No production enrollment has occurred.

HostAgent recovery now checks actual backup paths, refuses fresh-state fallback when orphan history exists, and publishes the new version floor to a protected recovery slot before the primary. Lower floor writes are rejected. Actual isolated state and signed-package selection checks passed.

Combined focused qualification: 74 tests passed. Separate recovery profile: 11 checks passed; five actual signed package-selection cases passed. A staged 0.5.12 forward-recovery package preserves all 0.5.10 runtime bytes and original provenance, with a new trusted signature. It has not been activated.

Remaining gates: trusted fresh-host identity provisioning; signed enrollment bundle binding the corrected sources and lifecycle integration; interrupted enrollment journal recovery; controlled disabled-task acceptance and enable/start; physical eject/reinsert and final operational checks. Protected-state qualification concerns single faults, not simultaneous loss of every valid state copy. Ubuntu follows remaining agreed milestones.
