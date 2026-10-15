# Prometheus Verification Council (PVC) — multi-reviewer plan
Date: 2026-10-14. **PLAN ONLY.** No runtime integrations, CI changes, installation, automatic merges, or additional GitHub App permissions authorized.

## Purpose
Check changes from Prometheus agents, Codex, external contributors and tools without assuming agreement by multiple AI models is objective proof. Independent measurable tests and fail-closed approval gates take precedence.

## Verification architecture
1. Intake on isolated branch or PR; classify affected capabilities and risk (normal/high/critical: auth, keys, backups, OS controls, network, privacy and self-modification).
2. Run deterministic checks: format/lint/type checks, unit/integration tests, CodeQL, zizmor for Actions, secret scanning, dependency vulnerability/SBOM and license scans; compare against prior baseline.
3. Ask orthogonal reviewers when justified: local reviewer + Codex + optional CodeRabbit under approved source-code disclosure policy. No need to invoke every reviewer on trivial diffs.
4. Normalize findings by file/line, category, risk, confidence, source, reproducibility and test evidence; deduplicate correlated suggestions. Preserve disagreement rather than blindly vote.
5. Reproduce high-risk issues with sandbox tests or minimal failing examples. Unverified AI claims become advisory, never silently accepted.
6. Require checks to pass, explicit owner approval for privileged/irreversible or sensitive changes, and a confirmed backup/rollback path before merge. Do not permit agent self-approval.
7. Record signed/hash-linked review receipts with changed commit SHA, tool versions, policies, results and approval provenance. Redact credentials and private contents.
8. After approved merge, deploy canary/staging first, observe metrics and roll back if regression triggers; keep previous known-good release.

## New candidate tools and duplicate strategy
| Candidate | Canonical upstream | Value | License / caveat | Dell/CI preference |
|---|---|---|---|---|
| reviewdog | https://github.com/reviewdog/reviewdog | Aggregate linter output into PR annotations, normalize review feedback | MIT; GitHub action permissions must be minimal | CI; Windows binary available |
| ast-grep | https://github.com/ast-grep/ast-grep | Tree-syntax search and custom coding safety rules | MIT | On-demand native local CLI or CI |
| OSV-Scanner | https://github.com/google/osv-scanner | Dependency vulnerability and license inventory | Verify actual release license and online database usage | CI, compare with existing Grype |
| Trivy | https://github.com/aquasecurity/trivy | SBOM, vulnerability and license scanning | Verify feature licensing and DB downloading | CI; alternative to Grype |
| Semgrep | https://github.com/semgrep/semgrep | Semantic security patterns and SAST | Mixed/community/commercial terms; clarify engine/rules/license | CI; compare with CodeQL/ast-grep |
| CodeRabbit | https://www.coderabbit.ai | External AI reviewer on PRs | Proprietary/cloud processing, code permissions, costs/retention review | Optional cloud only with owner consent |

## Resource scheduling
- Integrate with planned PARM predictive scheduler to run local scans only on changed code or when idle; outsource to GitHub CI when suitable.
- Cap concurrent reviewers, do not launch duplicate LLMs, avoid background daemons for single-use verification, resume after interruptions from durable checkpoints.
- Always preserve local/offline fallback: static checks, tests, signed review record and manual acceptance without CodeRabbit.

## Review policy tests
- Inject deceptive AI reviews and colluding/duplicated comments: agreement must not bypass tests.
- Validate critical-path policies: self-modifying code, updater changes, secrets, OS privilege escalations, recovery and backup changes all require independent approval.
- Test flaky CI, compromised dependencies, transient scans unavailable, false positives, malicious PR text, license conflicts and rollback failures.
- Capture wall-clock, CPU/RAM, network, cost and false-positive impact before enabling more reviewers.

## Governance
Original 79 candidate groups and Dell/SSD inventory remain unreconciled; candidate count provisional. No installs, code changes, or system access permitted by this document. Owner must separately authorize implementation.
