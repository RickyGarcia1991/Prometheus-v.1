---
phase: requirements
title: Prometheus offline chat
description: First runnable local chat milestone
---
# Requirements & Problem Understanding

## Problem Statement
The verified foundation provides health checks and backups but no runnable AI assistant. Ricky wants offline operation, local saves, and later online research using useful, attributable sources.

## Goals & Objectives
- Run text chat against a downloaded local Ollama model without cloud accounts or usage charges.
- Save successful conversation turns locally and resume a selected conversation.
- Preserve the independent Control Center and verified baseline.
- Non-goals: autonomous tools, OS replacement, voice UI, web research, semantic retrieval, model retraining, and guaranteed factual accuracy.

## User Stories & Use Cases
- Start Prometheus from a simple Windows launcher and ask a question.
- Close it, list saved sessions, and resume one without losing the conversation.
- Inspect local history without requiring Ollama to be running.
- Receive an actionable error if Ollama, the selected local model, or storage is unavailable.

## Success Criteria
- Existing eight foundation tests still pass.
- New black-box tests cover chat, local saves, resume, malformed responses, and refusal of cloud/external endpoints.
- A real llama3.2:1b response and persisted/resumed exchange succeed on this laptop.
- A final source snapshot verifies and the baseline is restorable.
- Launch instructions use copyable commands; a normal-user CMD launcher avoids PowerShell activation restrictions.

## Constraints & Assumptions
- Windows 11, approximately 7.6 GiB usable RAM and about 10 GiB free disk at baseline.
- Accepted starting assumption: llama3.2:1b is a low-resource baseline, not a high-accuracy research model.
- The existing 18.7 GB model is retained but not loaded.
- Python standard library only for runtime; existing pytest environment reused for tests.
- Chats are plaintext local SQLite data, not encrypted and not automatically trusted knowledge.
- User authorized necessary local changes; pause for paid services or cloud accounts.

## Questions & Open Items
No blocking questions for this milestone. Online source ingestion, provenance checking, retention preferences, and stronger models are explicitly deferred.
