# Dev53 preparation: interpretation revision and withdrawal

This candidate lets an owner revise or withdraw an approved historical interpretation using a new exact review. The original interpretation, native observations, failed checks and every attempted change remain visible. Competing active interpretations remain separate. These decisions do not run work or authorize remediation.

The separate append-only lifecycle store binds each attempt to the exact original interpretation and lineage. A newer linked attempt permanently prevents an older interrupted attempt from becoming effective, even when the newer attempt is rejected. A process restart preserves completed history and revokes pending reviews. Withdrawal is terminal for that interpretation; a separate fresh interpretation remains possible.

The grouped change covers project, worker and application evidence, current-input checks for revisions, stale withdrawal, bounded history, Unicode text, authenticated controls and rollback compatibility. The previous release can read the original historical interpretation but does not display the new lifecycle sidecar; preserving that sidecar restores the current view on re-upgrade.

Verified preparation evidence at this snapshot:

- 184 combined source integration checks and 108 interface checks passed.
- 32 independent checks passed, including real process exits, cross-process completion/link contention, an actual previous-release reader and malformed/late UI response cases.
- All 35 packaged UI suites passed.
- 270 isolated native Surface checks passed with no failures or skips. Executed source and results were read back by hash; production worker and identities were unchanged.
- Corrected generated fixtures passed native recovery and two actual packaged Interface status calls. The smoke used a synthetic owner, no model request and no disk-probe child process. Initial fixture and test-expectation failures remain in the public evidence record.

The signed candidate contains 1,027 payload files. Manifest SHA-256: `61d873757944044d83066462814aacffef5306de0e2d95e9bc5123134aeb4e73`. Archive SHA-256: `00e670e19d41e98d7b07f6b2fbb0e5c73488d09dd5f6907823ed95ac8591631c`.

This is a preparation snapshot. Complete packaged regression, normal-owner browser recovery and installation qualification require their own final records. This document does not claim that the candidate is installed or the broader roadmap is finished. The existing installed release and worker remain the active baseline until the coordinator completes qualification and deployment.
