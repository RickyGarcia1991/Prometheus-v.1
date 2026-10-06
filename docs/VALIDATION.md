# Validation — Prometheus 0.2.0, October 4, 2026

The active development source included uncommitted vocabulary and health-check changes.
Those changes were copied intact into a separate release candidate; the worktree was not edited.

- Baseline: 53 tests passed; launcher test failed because py was unavailable on PATH.
- Packaged Python fixed the launcher dependency. Final suite: 63 tests passed.
- Source/package compilation passed.
- Public search and backup/restore commands passed integration tests without Ollama.
- Search bounds, hidden-directory exclusion, exact citations and missing evidence passed tests.
- History backup: 2 sessions, 18 turns, SHA-256 verified. Restore to a new database matched.
- Backup refuses overwrite and checksum tampering; incomplete output is removed on timeout.
- A focused test rerun stalled during concurrent recovery activity; it was interrupted.
  Isolated rerun passed. SQLite connections now close explicitly and backup has a 30-second
  deadline so resource contention cannot cause an unbounded backup wait.
- Official Ollama 0.35.1 ZIP digest matched GitHub release metadata. Each recovered model blob
  matched its manifest digest/size. Only llama3.2:1b was included, with license blobs.
- Offline restore used separately extracted Ollama, packaged Python and recovered model on
  isolated loopback port 11435: doctor passed and real arithmetic returned 4 in 6.3 seconds.
- Eight live synthetic evaluation cases: 7 automatic passes, 1 exact-identifier failure.
  The planning answer also had a qualitative weakness. See EVALUATION.md.

Source ZIP/manifest, extracted-source checks, installed-release checks and launcher deployment
are recorded in the separate handoff report after this document is packaged.
No new coverage claim. Reboot/native-window appearance and another physical machine were not
tested. Recovery files are on this disk; disaster protection needs a separate private drive.
