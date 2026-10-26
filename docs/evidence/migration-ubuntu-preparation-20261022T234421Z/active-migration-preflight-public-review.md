# Public active HostAgent preflight qualification review

This review binds the frozen inspect-only source and test artifacts to successful normal-profile run `93544dacd5ff46b595bdcfc82f82deff`. No private SID, profile path, task context, authorization content, or worker identity is reproduced here.

The redacted run reports `preflight_observations_valid`, original R5 runtime, two observed owned workers, no launcher errors, and successful removal of its unique temporary inspection task. The final cleanup inventory records zero remaining inspection tasks. Inspection did not alter installed task/process/file state. The generated inspection task and workspace reports were the only intentional execution infrastructure changes.

I independently checked that the public report's worker and collector pins equal the current frozen files; its private-report digest matches the retained private report; that report's source pins match all three inspector dependencies; and run identity/status agree across both reports. These checks bind artifacts and observations without publishing private context. The worker held source read guards during inspection and published its completed report with create-new temporary file, flush/close, then atomic move.

The saved test evidence contains 61 passing cases (5.80 seconds). Coverage includes strict observation schemas, supported running/stopped baselines, observed competing session/worker refusal, package/receipt/auth/task/runtime refusal, pending-evidence handling, fixed read-only invocation and mutation-command scanning, eight tests of the actual extracted native task-shape predicate, and five integer creation-time precision boundary tests. The task-shape predicate requires an enabled Boolean logon trigger. The XML digest represents the captured current task definition; it is not a historical-receipt comparison. Root separately reported an independent rerun of all 61 cases passing in 5.90 seconds.

Earlier direct execution encountered file-level MSIX overlay redirection. Its hashes were not treated as installed-state damage. The finalized collector checks package identity, physical root, and each read file's final handle path. An initial normal-profile refusal exposed a floating-point creation-time comparison bug; the corrected predicate uses integer ticks and allows only native-to-CIM microsecond truncation. Both refusal evidence and cleanup evidence remain preserved.

This is an eligible observation under cooperative maintenance in one eligible interactive session, not mutation authority. `mutation_ready=false`, `production_ready=false`, and `cross_session_exclusion_proven=false` remain explicit. Execution must repeat stale observations under maintenance locks. No live migration was performed.

## Frozen artifact hashes

| Artifact | SHA-256 |
|---|---|
| `active_migration_preflight.py` | `29b1450e8e83316d59c60783ec8f0669cb8b0e0a1dbbed88f4fa278ded6fd1ad` |
| `inspect_active_host_migration.py` | `cec966b5dc36666e9272d8fd37c1ec5f7594cc6d86b9b3ce6040dde5eb35fff4` |
| `collect-active-host-migration-preflight.ps1` | `399606ea06f58a85c65c8d6c53a831c58bcfc893e35cbbb9ddeceafbff8fefcd` |
| `test_active_migration_preflight.py` | `77a917b7bce9d34002ac0f3bd889218b0b1e09a63cb115ad6e540b7fb547dc3a` |
| `run-active-migration-readonly.ps1` | `5eff42f3f490d1d9a13bfa1c9778a40f633a05cb2a8e0785cf13543e17680711` |
| `invoke-active-migration-normal-profile-inspection.ps1` | `f9ea48a162f548c0081632025ca24e38bd78b157fc6bdd38de94a7e735d01c5c` |
| `active-migration-preflight-tests.xml` | `8b837847989ea7be7c74fa948c635eae90bf0752af9d654135da928551d1946a` |
| `active-migration-preflight-tests.log` | `53dc5b53849b8b0af3328de2de00e191e3dd79495161897bc53971c0f591ee15` |
| `active-migration-preflight-results.json` | `685368874aafef21340c53f4c76d40eddba1dea44e7c86a33fffa6fd7199d061` |
| `active-migration-normal-profile-93544dacd5ff46b595bdcfc82f82deff.public.json` | `88f38b7bb951a29b363351fe1416bef1b2095b8edc8e1cd7feb46438a0e29319` |
