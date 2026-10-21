# Dev48: capability revisions and shared evidence provenance

Request changes now supports runtime settings, idle research, integration actions,
integration access, direct worker checks and scheduled checks. Feedback ends the
original proposal without executing it. The owner must explicitly prepare and
decide on a fresh replacement, which is linked to its parent in saved history.

Replacement context is scoped to the current owner, browser, conversation, kind
and subject, expires, and is consumed only after successful preparation. It does
not persist in conversation history or grant execution authority. Separate
sidecars preserve compatibility with the retained dev47 history reader.

Native worker observations can display bounded references to identical evidence
owned by the same owner. Matching requires exact job, request and evidence
identity; conflicting results and different owners cannot adopt each other's
observations. Shared references remain read-only.

## Validation

- Signed revision 2 package: 4,356 passed; 14 documented skips and one intentional
  existing-host identity exclusion. Complete collected test inventory verified.
- All 33 packaged browser-script suites passed.
- All 70 isolated installer/recovery checks passed.
- Actual Ubuntu Surface: 40 isolated revision/evidence checks passed; no production
  worker, enrollment, signing identity or ledger changed.
- Normally authenticated browser: all six feedback/replacement paths, exact new
  review identities, approved local compiler failure, denial without transfer,
  linked native outcomes and 12-decision persistence after reload passed.
- Actual retained dev47 reader verified generated dev48 sidecar compatibility.
- Post-test signature, 1,003 payload files, exact archive, baseline, frozen source
  and development source hashes matched. Read-only installation preflight passed.

The first complete package run found a bare test-interface fixture missing the
existing `_speech_owners` initialization. The corrected fixture passed 17 focused
checks and the complete rerun. The initial failure remains in the evidence.
Revision 2 changes that test fixture and provenance only; all app, dependency and
asset bytes match the browser/Surface-tested first candidate. Reuse is explicitly
bound by a complete signed-file comparison.

The remote browser destination and headroom were labeled fixtures. The scheduled
compiler check was native and did not execute submitted source. These checks do
not establish new model quality, general remote execution or wider autonomy.
Production installation and installed acceptance are tracked separately from this
source-validation record; the broader roadmap remains unfinished.

Candidate manifest SHA256:
`7a79e079dc0142ba4103081e4d8a949c3ad9b4238258e415d8a678b731315706`

Candidate archive SHA256:
`e336995413c6e90d6243a1447356df4f119e3dd1c35310cf77246cd0d2746e2d`
