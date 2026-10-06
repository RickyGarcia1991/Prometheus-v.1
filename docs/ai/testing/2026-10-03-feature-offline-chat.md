---
phase: testing
title: Offline chat verification
description: Recorded automated and real-device evidence
---

# Testing Strategy

## Test Coverage Goals
Cover all accepted offline-chat criteria with subprocess CLI tests and a fake loopback Ollama server. The unchanged eight foundation tests are included. Real-model checks use synthetic prompts only.

## Unit Tests
- [x] Loopback restriction, proxy bypass, redirect refusal, cloud tag and remote metadata rejection.
- [x] Invalid JSON, response objects, roles, incomplete/oversized replies, HTTP failure, inventory and connection failure.
- [x] Prompt limits, complete-pair context budget, missing model, unknown session, model mismatch.

## Integration Tests
- [x] Atomic Unicode user/assistant persistence and explicit resume.
- [x] Session/history inspection without any model server.
- [x] Unsupported SQLite schema preservation.
- [x] Interactive exit, EOF, empty input, prompt-error retry, and failed-answer no-save.
- [x] Windows CMD launcher --help from another working directory.

## End-to-End Tests
- [x] Real llama3.2:1b chat and resume in a separate process.
- [x] Doctor confirmed local model and writable default history.
- [x] Original foundation regressions.
- [x] Clean tracked-source archive, checksum/manifest verification, restored full suite (41 passed in 29.91 seconds), and baseline restore doctor.

## Test Data
Synthetic fixture prompts and isolated temporary SQLite databases. Real smoke data is in a separate validation database under local app data; it does not populate the default user chat history.

## Test Reporting & Coverage
Baseline: 8 tests passed in 0.54 seconds. Initial TDD red: 21 failed for the absent assistant entrypoint. Launcher red: 1 failed for the absent CMD launcher. Latest complete run: 41 passed in 39.90 seconds using coverage 7.16.0 with subprocess patching and combined child data. Branch-aware total coverage is 98%: CLI 98%, memory 97%, Ollama 100%. Remaining uncovered lines are Ctrl-C handling and the non-Windows default memory path; a defensive loop edge is partial. Parent no-data-collected warning is expected because assistant code executes in measured child processes.

## Manual Testing
The source launcher runs normally without PowerShell activation or policy changes. Desktop forwarding --help passed. A normal CMD chat process was started and remained running; native UI rendering was not visually inspected.

## Performance Testing
Actual saved/resumed exchange: 6.19 seconds first, 0.53 seconds resumed, 4 saved turns, 2 prior turns submitted. The initial direct Ollama cold check took 9.17 seconds. This 1B model may hallucinate and is not a high-accuracy research system.

## Bug Tracking
The optional ai-devkit task list probe returned unknown command; Markdown planning is authoritative instead. Initial package-directory creation was repaired before writing source. No unresolved acceptance-test failures.

## Release Gate Results
Compile checks, git diff --check, and ai-devkit lint (16 checks) passed. Clean source commit 6789d04 was exported and snapshotted; archive checksum/content verification passed, no chat databases/runtime data were present, and all 41 tests passed from its restored copy. The preserved pre-change ZIP also verified and restored doctor passed. An initial archive privacy regex overmatched .coveragerc; inspection showed only the configuration file, and the exact-data-file filter corrected the check without changing production code.
