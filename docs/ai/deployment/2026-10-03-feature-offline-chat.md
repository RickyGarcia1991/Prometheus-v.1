---
phase: deployment
title: Local Windows deployment
description: Desktop launch and reversible source recovery
---

# Deployment Strategy

## Infrastructure
Windows 11 laptop, Python 3.14.8, local Ollama 0.35.1, and downloaded llama3.2:1b. No hosting, subscription, paid service, cloud account, or CI deployment. Source stays on feature-offline-chat.

## Deployment Pipeline
Failing tests preceded new code and launcher. Run full pytest, combined subprocess coverage, compileall, git diff --check, ai-devkit lint, then real local chat/resume. Commit locally. Export tracked files, take a manifested snapshot, verify checksum/content, and restore into a new directory for a clean-source smoke test.

## Environment Configuration
Default API: http://127.0.0.1:11434. Default memory: %LOCALAPPDATA%\Prometheus\memory.sqlite3. Global --memory and --model options precede the CLI command. Alternate endpoints must remain loopback; no cloud fallback. Existing local Node/npm are available but not runtime dependencies.

## Deployment Steps
Double-click the Desktop Start_Prometheus_Offline.cmd or source START_PROMETHEUS.cmd. The Desktop wrapper points to this worktree; moving/deleting it will break that wrapper. Use /exit to stop. List sessions and explicitly select a session ID to resume.

## Database Migrations
Schema version 1 only. Unknown versions are rejected; no automatic migration or reset. Chats are plaintext outside source. Source archive recovery does not restore conversations or models.

## Secrets Management
No API keys are needed. Do not put private chats or secrets in source. Clean tracked-file exports are used for the handoff archive. No credential extraction or public Git push.

## Rollback Plan
Close local chat. The unchanged original main source and before-offline-chat verified ZIP are preserved. Extract backups into a new folder rather than over user files. Point a launcher at the restored source if needed. Restore local memory only from a separate known backup, with the assistant closed.
