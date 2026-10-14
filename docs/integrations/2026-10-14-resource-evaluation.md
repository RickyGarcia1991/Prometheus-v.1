# Prometheus integration candidates — 2026-10-14

Status: **PLANNING ONLY. No integration code, downloads, installations, configurations, or tests are authorized until the Dell is connected and the owner asks to proceed.**

All listed candidates are future possibilities subject to verifying the full project, existing 79 candidate groups, and upstream sources. Research additions do not imply approval to deploy.

This branch deliberately does not assume the GitHub main branch reflects the active Dell/SSD build. Before merging, inventory the Dell workspace, preserve a verified snapshot, reconcile the full 79-group candidate inventory, and port additions to the actual runtime architecture.

| Candidate | Upstream | License | Role | Default state |
|---|---|---|---|---|
| zizmor | https://github.com/zizmorcore/zizmor | MIT | Audit GitHub Actions and CI scripts | OFF; run in CI when reviewed |
| sqlite-vec | https://github.com/asg017/sqlite-vec | MIT OR Apache-2.0 (confirm checked release artifacts) | SQLite-backed vector retrieval | OFF; require pinned loadable extension |
| Kopia | https://github.com/kopia/kopia | Apache-2.0 | Independently verifiable backup/snapshot destination | OFF; must configure key and destination separately |
| Hush | Verify original vendor/repository URL against previously retrieved candidate before use | Reported Apache-2.0; reverify | Audio architecture reference and experimental adapter | OFF; no Windows runtime assumed |
| uv | https://github.com/astral-sh/uv | MIT OR Apache-2.0 | Locked, reproducible Python environments | Evaluation only |
| Grype | https://github.com/anchore/grype | Apache-2.0 | Scan software dependencies for known vulnerabilities | Evaluation only |
| Syft | https://github.com/anchore/syft | Apache-2.0 | Build software bill of materials (SBOM) | Evaluation only |

## Gates before promotion
1. Compare all entries with the authoritative 79-group inventory and the Dell deployment manifest; reject duplicates.
2. Pin versions and verify upstream signatures/checksums when available. Review transitive dependencies, licenses, and source changes.
3. Use a clean isolated test environment with network access off by default; record CPU/RAM/IO peak on Dell 16 GB Windows.
4. Run unit/integration tests, failure-injection, backup restore drill, and crash/restart tests. Protect existing data with a signed or independently hashed baseline snapshot.
5. Obtain owner approval for any elevation, OS changes, new accounts, remote APIs, spending, sensitive data export, or destructive actions.
6. Never ship development integrations enabled by default; require an explicit feature flag and easy rollback.
7. Merge only after verifying Dell local project matches the inspected Git tree.

## Specific acceptance tests
- **zizmor:** run read-only against .github/workflows; review findings and suppressions, confirm no workflow rewrite.
- **sqlite-vec:** test insertion, search, data provenance, delete/forget propagation, corruption handling, and performance against native SQLite FTS baseline. Avoid prerelease ANN in production.
- **Kopia:** verify encrypted backup and restore into a new directory; keep keys/passwords outside SSD, backup media, logs and Git. Do not automatically replace verified existing backup providers.
- **Hush:** inspect source, Windows packaging and memory profile; check microphone permission, consent, offline processing and leak-free start/stop cycles.
- **Grype/Syft:** evaluate as an optional on-demand supply-chain scan, not as a background resident service.
- **uv:** evaluate for lockfiles and reproducible, isolated Python setups; do not overwrite active Python or Ollama installations.

## Hard operational constraints
- No deletion of existing code or automatic migration of memory.
- No unrestricted process killing or disabling OS security and update services to save RAM.
- No scanning other hosts/networks without scoped authorization.
- No secrets in telemetry, build logs, snapshots, Git issues, or repository content.
- Backups, telemetry, and approval gates must fail closed for privileged actions.
