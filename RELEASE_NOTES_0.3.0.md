# Prometheus v0.3.0 — orchestration, structured memory, and verified lifecycle

Prometheus v0.3.0 advances the local-first assistant foundation with explicit orchestration,
provenance-aware research, structured knowledge storage, and safer portable lifecycle behavior.

- Adds LOCAL vs RESEARCH routing and per-turn research decisions.
- Adds opt-in HTTPS research with source provenance and an untrusted-evidence boundary.
- Adds schema-v2 structured knowledge for preferences, decisions, facts, and instructions.
- Preserves provenance, confidence, retention policy, and superseded knowledge history.
- Migrates schema-v1 conversation history to v2 without losing saved sessions or turns.
- Extends verified backup/restore to preserve structured knowledge.
- Adds graceful chat shutdown through a shared shutdown marker and END_PROMETHEUS.cmd.
- Detects chat mode even when global CLI options precede the chat command.
- Verifies the local llama3.2:1b lifecycle and keeps cloud fallback disabled.
- Keeps automatic conversation-to-knowledge retention disabled pending an explicit trust policy.

Validation at release promotion: 87 tests passed; doctor passed local model, history integrity,
cloud-fallback-disabled, and vocabulary checks. Portable external-drive and flash-drive lifecycle
testing also reached Running and clean ReadyToEject states with verified checkpoints.

Prometheus remains in 0.x initial development. Voice, autonomous tools, semantic retrieval,
automatic trusted-memory extraction, model retraining, and physical hardware control remain
future milestones.
