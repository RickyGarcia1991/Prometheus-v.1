# Prometheus v0.2.0 — recovery and local document search

Local chat now ships with a portable Python runtime, verified conversation backup/restore,
and local UTF-8 document search with exact file and line citations.

- Preserves existing offline chat, vocabulary guidance, sessions and explicit resume.
- backup-memory uses SQLite's backup API and records SHA-256 plus session/turn counts.
- restore-memory verifies checksums, integrity and counts; it only writes a new path.
- search reads .md, .txt and .csv in an explicitly selected directory without a model.
  It reports literal excerpts, missing evidence and skipped files; it never invents an answer.
- Search limits: 500 files, 1 MB per file and 20 MB total; hidden directories and links excluded.
- The launcher prefers packaged Python, removing its dependency on py PATH resolution.
- Offline recovery materials contain the selected model and official Ollama runtime separately
  from the source release and private conversation backup.

See docs/VALIDATION.md for fresh execution evidence and limitations.

Earlier milestones: foundation (8 tests), offline chat (41), vocabulary/health maintenance (54).
