# Dev50: dashboard freshness and application evidence

Manual Refresh decisions now reads the passive dashboard immediately, including within its background polling interval. Completion of connected-computer updates also triggers a passive read. Concurrent notifications coalesce, older responses are discarded, and normal authentication, visibility and busy gates remain. Device observations still expire after 30 seconds; refreshing the dashboard does not refresh the provider or turn stale information into a current reading.

New application-change decisions retain the exact owner, approval, job and candidate before promotion. A bounded separate sidecar stores native activation observations and presents pending, active, failed, restored, recovery-required and conflicting historical results. Original failures survive restoration; contradictory facts have no automatic winner. Legacy records without an exact link remain unconfirmed. History cannot authorize, replay, sign or activate work, and recorded activation does not imply the currently selected release is healthy.

The release contains 18 reviewed source/test/documentation changes against signed dev49 revision 2. No Surface production worker, signing identity, existing project evidence store or model-acceptance policy changes.

Validation on the exact signed candidate:

- 4,480 packaged Python tests passed; 14 documented skips and one fixed native-signing exclusion retained. Complete selected-test identities and phase results verified.
- All 33 packaged interface suites passed; portable Python smoke and post-test source/tree/archive integrity passed.
- 127 isolated Linux Surface tests passed with pinned free dependencies and verified results; no production worker changed.
- 111 installer/baseline checks and nine separate application-evidence acceptance-helper tests passed. The initial test-path/environment setup failures remain preserved; operational installer and application bytes were unchanged by those corrections.
- Normally authenticated browser checks confirmed the nine labeled generated owner-history records, failure/conflict severity, unchanged history after reload, immediate manual rendering, reconnect/identical-inventory refresh and natural expiry. Copied native stores retained every raw fact with no execution authority or dispatch.

One live observation is explicitly limited: the offline device list was observed, but its immediate dashboard transition coincided with normal busy-state clearing. That specific live assertion is not counted as observed; focused unit/backend checks cover the transition. Manual browser proof records new rendering within 30 seconds, complemented by request-count and race tests, rather than claiming captured response bodies.

Candidate manifest: `ef125f89a814e2f61c478d9df377d2cdf27d625a34e37a029de88b6380e7b2a3`.

Archive SHA256: `922a69d444a8a2a5ca9090345497adf16085ce0da0d19450ab67e3af7c33b6b0`.

1,010 signed payload files. Installation is separately recorded with a fresh verified backup/restore and dev49 revision 2 retained for rollback. The broader roadmap remains unfinished: conflict-resolution proposals and other workflow families, additional scheduler measurements, broader reviewer acceptance, update/requalification lifecycle, and heterogeneous device cooperation remain.

## Historical installation interruption — 2026-10-19

The authorized installation verified a fresh 184-file backup and isolated restore, plus all three signed application copies. Its six applicable native health checks passed. USB-storage reset and disk-retry events then interrupted activation; the installation journal remains incomplete, and the installed interface has not passed acceptance. This is not recorded as a successful installation. The original journal and diagnostics are preserved; dev49 revision 2 and verified recovery copies remain available.

The full package qualification above is unchanged. Recovery must first restore stable storage access and reconcile the existing transaction, then complete startup, retained-history and installed-interface acceptance. No second installer has been run and no worker or security settings were changed. A separate flash checkpoint verified 344 public files, including the signed archive and failure evidence, without accessing the stalled SSD. The final SSD evidence checkpoint is pending.

## Installation acceptance completed — 2026-10-19 03:09 UTC

Storage access resumed and the original one-use installer completed successfully. No second installer or separate recovery operation was run. The earlier interruption snapshot and emergency flash checkpoint remain unchanged. All three signed copies, the 184-file backup/restore and retained dev49 rollback verified. Startup, all nine native installed acceptance receipts and the owner-authenticated installed dashboard/history/reload checks passed. The three retained worker results are unchanged, including the original failed syntax example; no new production job was submitted by acceptance.

The latest bounded storage check showed both expected removable-volume identities and no new reset within two minutes. This is current responsiveness, not a claim of permanent USB hardware repair. Prometheus's provider connection is disconnected at acceptance; the separate Surface monitor connection is not counted as a connected application provider. Full qualification and its stated observation gap are unchanged. Final storage checkpoint receipts record the copies of this completed evidence and the preserved interruption record.
