---
phase: implementation
title: Offline chat implementation notes
description: Implemented local-only chat with durable sessions
---

# Implementation Guide

## Development Setup
Work on feature-offline-chat in the isolated .worktrees directory; the original foundation remains on main. Runtime requires Python 3.11+ and installed local Ollama, with no third-party Python runtime dependencies. The original project virtualenv supplies pytest and coverage 7.16.0 for development only.

## Code Structure
The new prometheus_assistant sibling package contains CLI, local-only Ollama transport, and SQLite conversation persistence. prometheus.py runs from source. START_PROMETHEUS.cmd uses py -3 directly, so PowerShell script activation is unnecessary. A Desktop Start_Prometheus_Offline.cmd forwards to this worktree. The Control Center production code is unchanged.

## Implementation Notes
Twenty-one assistant black-box tests failed before production code existed, then passed. The Windows launcher test also failed before its implementation, then passed. Additional regression tests exercise errors, complete-pair context trimming, schema version protection, and interactive recovery. The current complete suite has 41 passing cases.

## Integration Points
Only HTTP loopback /api/tags and /api/chat; no proxies or redirects. The selected installed tag is llama3.2:1b. Local SQLite defaults to %LOCALAPPDATA%\Prometheus\memory.sqlite3. Successful user/assistant pairs save in one transaction. Full history is retained, but at most the last eight turns fitting a 6000-character context budget reach the model. Resume is explicit and must use the original model.

## Error Handling
Invalid or unfinished answers do not save turns. Cloud names/remote metadata and external endpoints are rejected. Inventory, connection, storage, prompt-length, and unknown-session errors are actionable. Unsupported memory schema versions are refused without migration.

## Performance Considerations
Synthetic real-device chat returned the verification word marigold in 6.19 seconds. A fresh process resumed the selected session and recalled it in 0.53 seconds; four saved turns and two prior context turns were verified. These are observations, not benchmarks or guarantees.

## Security Notes
No model-generated tool execution, browser research, paid service, new cloud account, or system execution-policy change. Chat history is plaintext and not verified factual knowledge. The existing large model was retained and not loaded. The verified pre-change archive remains available.
