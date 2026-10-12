# Prometheus 0.8.0.dev3 — main workstation and capability upgrade

Adds an explicitly pinned main-PC signer alongside the original signer, with an original-key-authorized migration and portable recovery. Private signing keys remain on their Windows hosts. See `docs/CAPABILITY-UPDATE.md`.

Adds source-based answers in the interface, explicit local image questions and
capability/schema discovery. Improves project search, citation provenance, model
fallbacks, output budgeting, benchmark eligibility and warm-model reuse. Protects
installed source from default replacement tools and preserves undo in selected
development workspaces. Imports remain immediately keyword-searchable without
automatically loading an embedding model; use the indexing action when needed.

This is a development candidate. Activation requires the original signing
identity and a coordinated update of the SSD bootstrap and recovery references.
No model weights were trained. See `docs/CAPABILITIES-0.8.0.dev2.md`,
`docs/ARCHITECTURE.md` and `docs/CAPABILITY-UPDATE.md`.

# Historical milestone: v0.2.0 — recovery and local document search

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
