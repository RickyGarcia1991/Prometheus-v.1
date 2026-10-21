# Dev47: connection freshness in Decisions & health

The dashboard now distinguishes a recent provider observation from expired or unavailable device information. It shows bounded online/offline/unknown counts and selected-device state using fixed text. Saved job results remain historical evidence, separate from current connectivity.

RemoteAccess caches successful inventory with wall and monotonic timestamps. Both clocks must remain within the 30-second window. Failures, reconnects and generation changes cannot retain an affirmative current-online claim. Reading the dashboard does not call the provider, expire pending approval or dispatch work. Browser rows expire at the observation deadline.

Validation for the signed 992-file candidate:

- 4,288 packaged Python tests passed, 13 documented platform/context skips, one explicit production-signing-identity test excluded. Exact collected cases, terminal outcomes and JUnit identities were matched.
- All 32 packaged UI suites passed; portable Python imports, signature, tree and archive verified.
- 259 affected tests passed; independent review separately ran 172 Python checks and 16 UI checks without actionable findings.
- Eighteen native Ubuntu Surface checks passed using exact candidate source bytes and generated provider data, with no production worker changes.
- Owner-authenticated browser preview checked current and stale observations, generated offline/absent inventory, provider errors, reconnect, selected-online status and reload/disconnect behavior. Dashboard reads and reloads added no provider inventory calls.
- Sixty-nine isolated installer tests passed. Production preflight, installation, startup and owner-authenticated installed-interface acceptance are complete.

The test launcher was tightened after independent review identified that inherited pytest options could narrow a run. It now refuses such options and requires complete case-level evidence. No signed application change was required for that launcher correction.

Preview background model-metadata update checks reported timeouts. Those warnings stayed visible throughout the connectivity tests. This feature qualification does not claim that all external update/model services are healthy, nor establish broader model reviewer acceptance.

Manifest SHA-256: `56e0d76e73c4e523213b8743f1bde323d03cd80f17430ced9f54a5148faea5d5`

Archive SHA-256: `60dc3a7cde205dee83971cba90fed5f7188c36828cc2a517e332edb354e13b03`

This is one bounded milestone in the unfinished Prometheus roadmap. Existing provider credentials, production worker identities, owner authentication and exact-action approvals remain unchanged.

## Installed acceptance and recovery

Dev47 is active with dev46 revision 2 retained for rollback. A fresh 181-file backup and isolated restore verified. All three signed copies contain the exact 992-file payload. Saved results and claims match the fresh pre-installation snapshot, with no test dispatch or replay. Installed native measurements, six mode cases, decision-history/revision recovery and 18 connection-health cases passed. The owner unlocked the installed interface; version, saved conversations, historical failure/pass evidence, current disconnected state and reload behavior were verified.

Two earlier activation attempts encountered Windows access denied while replacing the portable launcher. Both journals and failure records remain preserved. The second attempt restored dev46 successfully. After a scoped restart of Desktop Commander's idle local stdio child, the signed-in remote parent remained, both device connections worked, and native checks showed no registered resource users for the activation files. A final continuation with a separate journal, new verified backup and 30 independently passing isolated tests completed activation. No ACL or security settings changed; the evidence does not establish the definitive cause of the Windows refusal.

Both lightweight progress monitors are serving. The installed app's own provider session reports disconnected after restart; this is distinct from the online external Desktop Commander links. Real in-app remote work requires its ordinary account connection. The Surface worker was not upgraded by this release.
