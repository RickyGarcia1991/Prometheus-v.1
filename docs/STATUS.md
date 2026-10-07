# Status — Prometheus 0.3.0

Delivered: local llama3.2:1b chat, vocabulary guidance, saved sessions and explicit resume;
loopback-only transport; verified history backup/restore; local document search with literal
file/line citations; portable Python; stable versioned deployment and recovery materials.

See VALIDATION.md for fresh checks and EVALUATION.md for real-model measurements.
The earlier 98% coverage measurement is historical; coverage was not remeasured here.

Search is lexical evidence retrieval, not semantic retrieval or verified model knowledge.
Opt-in per-turn online research routing and provenance boundaries are implemented when an HTTPS
research provider is configured. Schema-v2 structured knowledge stores explicit preferences,
decisions, facts and instructions with provenance and retention metadata; automatic retention is
not enabled. History remains plaintext. Voice, autonomous tools, semantic retrieval and model
retraining remain future work. Development changes and user history are preserved. Private
backups remain in local app-data, outside shareable deliverables.

The Desktop launcher now uses a versioned app-data release, independent of the feature worktree.
See RECOVERY.md for restoration, private-history handling and rollback.
