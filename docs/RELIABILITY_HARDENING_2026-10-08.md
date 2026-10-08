# Reliability hardening — October 8, 2026

This increment adds a reproducible, read-only reliability smoke test using temporary synthetic data. Run `py -3 tools/reliability_report.py` or pass `--output PATH` to save a machine-readable JSON report. The test checks SQLite integrity, verified backup and restore, research-note persistence, trusted-knowledge separation, and unsafe research source rejection. It does not access production chat history, measure answer quality, or test a live external research provider.

Verified history backup metadata now records the count of retained untrusted research notes. Restore continues to accept older backup manifests without that count, while validating any counts that are present. Existing backup destinations are never overwritten.

Live research provider configuration and physical USB flash-drive eject/reinsertion remain separate acceptance tests requiring a real provider and an attached removable drive. The primary 2TB Prometheus SSD is not the removable recovery flash drive. Do not claim either test passed based on this offline fixture.

Security review basis: OWASP LLM Top 10 (2025), including prompt injection, excessive agency, and unbounded consumption. These are engineering test priorities, not a security certification.
