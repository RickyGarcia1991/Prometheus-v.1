---
phase: monitoring
title: Local verification and troubleshooting
description: No external telemetry or monitoring account
---

# Monitoring & Observability

## Key Metrics
CLI reports measured request elapsed time and saved-session ID. One synthetic real-device exchange took 6.19 seconds; a resumed one took 0.53 seconds. Treat these as examples, not performance promises.

## Monitoring Tools
Use START_PROMETHEUS.cmd doctor for model/storage checks, sessions for stored session counts, and history SESSION_ID for explicit history review. Use Ollama's existing local interface and Windows Task Manager for resource checks. No external analytics, telemetry integration, dashboard, or subscription was added.

## Logging Strategy
Successful exchanges are saved in local plaintext SQLite. Failed model requests do not save an assistant turn. No private prompts are copied into source verification reports. There is no background service or automatic retention policy.

## Alerts & Notifications
Errors appear in the terminal. There are no automatic alerts or scheduled tasks. Do not silently substitute cloud models if local generation fails.

## Incident Response
If Ollama cannot be reached, open the installed Ollama app and retry. If the local tag is missing, download llama3.2:1b while online before offline use. For storage/schema errors, preserve the database; do not delete or automatically migrate it. For RAM pressure, close unused apps and keep the smaller model selected. For factual uncertainty, verify with authoritative sources; this milestone cannot browse.

## Health Checks
41 automated tests and a real persisted/resumed local exchange passed. Snapshot integrity and clean-source restore are release gates. No online account is required for routine local checks.
