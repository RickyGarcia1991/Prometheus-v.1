# Status — Prometheus 0.2.0

Delivered: local llama3.2:1b chat, vocabulary guidance, saved sessions and explicit resume;
loopback-only transport; verified history backup/restore; local document search with literal
file/line citations; portable Python; stable versioned deployment and recovery materials.

See VALIDATION.md for fresh checks and EVALUATION.md for real-model measurements.
The earlier 98% coverage measurement is historical; coverage was not remeasured here.

Search is lexical evidence retrieval, not semantic retrieval or verified model knowledge.
History is plaintext. Web research, voice, autonomous tools, retraining and automatic retention
remain future work. Development changes and user history are preserved. Private backups remain
in local app-data, outside shareable deliverables.

The Desktop launcher now uses a versioned app-data release, independent of the feature worktree.
See RECOVERY.md for restoration, private-history handling and rollback.
