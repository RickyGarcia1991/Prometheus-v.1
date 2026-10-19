# Dev52 evidence interpretations — tested candidate

Owners can review an exact interpretation of conflicting recorded evidence.
The review binds the selected native fact, collected input snapshot and reason
to the current owner session. Approval rechecks those inputs and appends the
interpretation without erasing original observations, failures or contradictions.
Rejection remains visible. An interrupted completion is unconfirmed; restart
does not recreate a pending review or replay work. Competing completed approvals
are displayed as competing interpretations, without selecting a winner.

The existing receipt schema is retained. A bounded additive sidecar records
interpretation history. Signed dev51 read the new generated history without
changing bytes or restoring pending authority. Backup/restore preserved both
completed and unconfirmed interpretations.

## Verification

- Full signed Windows package: **4,581 passed, 14 skipped, one exclusion**;
  exact test collection and JUnit inventory matched. One skip occurs at collection.
- All **34 packaged UI suites passed**.
- Ubuntu Surface: **206 passed**, no failures or skips, exact result readback
  verified; production worker unchanged.
- Isolated installer/helper checks: **132 passed**.
- Normal-owner browser: approve, reject, reload, saved history and process restart
  passed. An undecided review was absent after restart; completed history and
  every original observation were preserved.
- Post-test signature, 1,020-file payload, archive and frozen source verification
  passed. The installed dev51 baseline remains unchanged.

The browser examples are labeled generated history, not model acceptance or real
worker execution. The original failed fixture check is retained. Normal status
polling added one unconfirmed observation; the corrected external verifier checks
the exact original seed digest and includes the addition in the live history.
Seven verifier checks passed. Application bytes did not change for that correction.

Skips cover unavailable Windows symlink privileges, Linux-only native limits and
an offline signer migration preparer outside the application payload. The single
inherited exclusion requires a different native-host identity; it is not a new
feature test. All records remain in the release evidence.

## Remaining release work

This is a draft tested candidate, **not installed**. The direct source review is
recorded; independent agents exhausted their usage allowance before an audit
could finish, so an independent audit is not claimed. Final installer review,
installation preflight, fresh stopped-data backup/restore, activation and installed
interface acceptance remain required. The existing installer validation still
requires independent review; no passing flag has been invented or waived.

Dev51 is the intended rollback release. Existing identities, owner data and the
Surface production worker are retained. The wider roadmap remains unfinished.

Manifest SHA256: `63727623b0180ff6496785b35584c03198c84008c16c5eeecf07f1165fa0a59b`

Archive SHA256: `605bddda02e241130477f7ed59e116367c561b5b5af36377acab339e1114ff0c`
