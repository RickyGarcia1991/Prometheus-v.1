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


## Approved future design additions (2026-10-14)
These are accepted into the **planning backlog** and are not yet active components.

### Adversarial reviewer
- An independent hostile-case reviewer examines proposed changes for approval bypass, privilege escalation, unsafe dependencies, injection, exfiltration, backup corruption, data loss and resource exhaustion.
- Reviewer output is evidence to verify, not permission to execute attack code on real systems or a substitute for owner judgment.
- Offline sandbox and narrowly scoped fixtures only; no probing third-party networks.

### Autonomous test generation
- Derive property-based, boundary, regression, integration and fault-injection tests from requirements, previous incidents and reviewed changes.
- Validate tests against requirements and intentionally faulty variants where safe; prevent generated tests from mutating real user data or system configuration.
- Require independent reviewer confirmation before generated tests become trusted release gates; track test provenance and false-positive rates.

### Verified rollback intelligence
- Prepare a signed/hash-verified last-known-good snapshot and migration rollback plan before approval to deploy.
- Track post-change health, performance, memory integrity, backup recoverability and regression signals.
- Recommend or execute only explicitly pre-authorized bounded rollback steps; irreversible recovery actions and operations affecting private data or security boundaries require owner approval.
- Never silently overwrite newer user data with an older snapshot.

## Owner governance directive
The creator/owner is the **final approval/denial authority for security-sensitive decisions** relating to Prometheus. Other agents, reviewers, and automations cannot override this authority, approve their own privileged actions, or redefine security boundaries.

- Allowed automatically at the **planning** level: identify and record further candidates, architecture improvements, test ideas, performance optimizations and research questions, with reasons and confidence, subject to later review.
- Non-security ideas may be included in the future plan without separate approval each time; **listing is not installation or implementation permission**.
- Explicit owner approval is required before enabling security controls, changing permissions, remote access, network exposure, secrets/keys, account integrations, surveillance capabilities, updates to security policy, potentially destructive file/OS operations, automatic merges/deployment, or changes with meaningful privacy or financial impact.
- Baseline safety mechanisms must remain intact, including backup integrity checks, isolation and no unauthorized actions; owner authority is exercised through authenticated, logged confirmation, not implied consent or reviewer voting.
- On uncertain risk or disputed classification, classify as security-sensitive and request owner decision. Provide an understandable action preview with scope, affected assets, effects, reversibility and rollback.
- Do not treat approvals granted in one context as standing permission across different future devices, tasks or scopes.
