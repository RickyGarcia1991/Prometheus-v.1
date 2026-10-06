---
phase: planning
title: Offline chat implementation plan
description: Ordered tasks and verification gates
---
# Project Planning & Task Breakdown

## Milestones
- [x] Verified foundation tests, baseline archive, local model download, and isolated feature branch.
- [x] Runnable local chat with durable sessions.
- [x] Verified source archive, clean-source restore, baseline restore, launcher, and regression tests.

## Task Breakdown
### Phase 1: Foundation
- [x] Preserve baseline commit cfae6e9 and before-offline-chat archive.
- [x] Select feature-offline-chat worktree and record scope/design.
- [x] Write black-box failing tests before assistant production code.
### Phase 2: Core Features
- [x] Implement local-only Ollama transport and bounded context.
- [x] Implement atomic SQLite session/turn persistence and resume.
- [x] Implement chat, ask, doctor, sessions/history, and error paths.
### Phase 3: Integration & Polish
- [x] Add launcher and accurate README/status documentation.
- [x] Run new and original tests, compile checks, and real-model saved/resumed smoke.
- [x] Verify final snapshot and restore test, review diff, and commit locally.

## Dependencies
Python 3.11+, downloaded llama3.2:1b, and running local Ollama. Runtime has no pip dependencies. Existing virtualenv supplies pytest. The optional ai-devkit task CLI probe returned unknown command; tracking is maintained here instead.

## Timeline & Estimates
Complete gates in order; do not replace verification with a time promise.

## Risks & Mitigation
RAM pressure: smallest downloaded model and bounded context/output. Hallucinations: explicit uncertainty and no verified-knowledge claims. Privacy: local app-data memory, no Git/source archive inclusion. Reversibility: original baseline and separate worktree. No cloud fallback.

## Resources Needed
Connected Windows laptop and local development tools. No paid service or new cloud account.
