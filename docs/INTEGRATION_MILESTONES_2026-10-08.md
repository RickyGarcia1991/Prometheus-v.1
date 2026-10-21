# Integration acceptance milestones

## Completed and tested in this increment
- Research provider endpoint validation now rejects malformed HTTPS URLs, credentials, and fragments. Redirect attempts are denied by a dedicated redirect handler. Research remains opt-in; a real provider has not been configured.
- Repeated identical structured knowledge writes with matching provenance, confidence, and retention are idempotent. Changes retain supersession history.
- Full regression suite and synthetic offline recovery diagnostics remain mandatory before deployment.

## Acceptance gates still open
1. Live HTTPS JSON research provider: configure a compatible endpoint with user-approved credentials if required; verify source provenance and outage behavior. No provider or paid account is silently provisioned.
2. Recovery flash drive: identify the physical removable device, obtain confirmation of its identity, perform safe copy and read-back verification, then supervised eject/reinsert. Never format unknown drives.
3. Memory quality: add explicit contradiction review and relevance tests on realistic, consented datasets; synthetic dedup alone is not semantic correctness.
4. Autonomy: keep mutation, network, and command execution behind explicit permission and auditable logs; no unsupervised deployment.
5. Performance: record real model latency, peak memory, and answer quality on a fixed evaluation set; fixture timings are not model benchmarks.
6. Self-improvement: require Git branch, review, automated tests, explicit approval, and rollback before promoting proposed code changes.

No claim of fully autonomous or production-grade integration is made by this checkpoint.
