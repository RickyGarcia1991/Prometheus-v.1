# Prometheus Adaptive Resource Manager (PARM) — research proposal
Date: 2026-10-14. Status: **PLAN ONLY**; do not install, run, modify Windows settings or merge.

## Goal
Coordinate models, memory, CPU, GPU, battery, disk I/O, and scheduled background work on the Dell Windows 16 GB primary laptop and other authorized lower-spec devices; minimal idle footprint with demand-driven scaling.

## Recommended layered design (not a giant additional AI service)
1. **Observability**: psutil (https://github.com/giampaolo/psutil; BSD-3-Clause) process, RAM, CPU, disk and battery measurements; Windows performance APIs where appropriate. No high-frequency telemetry unless demanded.
2. **Model residency**: reuse Ollama's keep_alive, unload-on-demand and model limits (https://github.com/ollama/ollama/blob/main/docs/faq.mdx). Compare optional llama.cpp router and sleep-idle-seconds, models-max (https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md). Avoid duplicate resident engines.
3. **Windows containment**: Windows Job Objects enforce limits on PARM-launched processes (https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects); Microsoft EcoQoS for background owned processes (https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation). Restore defaults after task completion; avoid harmful hard limits causing failures.
4. **Background tasks**: assess APScheduler MIT licensed stable v3 line (https://github.com/agronholm/apscheduler), journaling and checkpointed durable queue. v4 prerelease is NOT recommended for production as of research date.
5. **Cross-device scheduling**: initial lightweight opt-in resource advertisements and signed task handoff; devices retain local veto, owner privacy and battery thresholds. Do not introduce Ray without a compelling distributed workload need: logical resource requests do not enforce physical CPU/memory limits (https://docs.ray.io/en/latest/ray-core/scheduling/resources.html).
6. **AI-aware workload inspector candidate**: https://github.com/AlejandroDeBlas/local-ai-task-manager — inspect maturity/security/license and Windows GPU relevance before adoption; optional diagnostic tool, not required daemon.

## Decision policy
- Modes: Idle / Balanced / Performance / Battery Saver / Critical Recovery.
- Prioritize foreground conversation and explicit owner tasks over scheduled research and indexing.
- CPU/RAM/IO budgets and hysteresis avoid switching thrash. Account for committed memory, memory pressure, GPU memory, disk free space, thermal throttling, charge state and power profile.
- Prefer on-demand loading, timeouts and unloading of larger models; unload after task boundaries and cache intelligently when ample memory.
- When resource-starved: defer optional work, reduce concurrency, choose lighter model, checkpoint safe jobs, request user approval where necessary.
- Coordinate cross-device execution only with verified device opt-in and authenticated transport. Never wake devices, access microphones/cameras or consume another person's hardware by default.
- Preserve Windows security/update services, active user programs, filesystem safeguards and backups; no automatic arbitrary process kills or indiscriminate service disabling.

## Test plan for connected Dell
- Inventory actual existing code and 79 resource groups to avoid duplicates; check baseline model APIs and hardware (16 GB expected, confirm actual).
- Benchmark unloaded idle overhead, time-to-response, model loading peak RAM, CPU utilization, battery drain, thermal readings and task queue behavior.
- Simulate low RAM, low battery, power disconnect, loss of network, sleep, crash/restart, disk-full and missing device. Restore queued jobs without duplicating irreversible actions.
- Verify explicit approvals, audit logs, fail-safe reversion of process controls, no unauthorized process manipulation and feature-flagged rollback.
- Compare psutil-based minimal controller versus Ray or third-party always-on managers based on measurable ROI.

## Acceptance criteria
- Negligible idle overhead (set numerical target after baseline), stable interactive latencies, no hangs or OOMs under bounded model workloads.
- Documented per-device policies and test measurements, secure rollback, idempotent recovery, reliable sleep/wake and user override.
- No implementation until separate express authorization when Dell is connected.
