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


## Expanded mandatory operating modes (2026-10-14)
These five modes are required user-facing and internal policies, not separate OS power plans. Automatic mode switching is allowed only within user-defined limits; manual override and minimum dwell times prevent oscillation.

| Mode | Activation | Behavior | Safe exit |
|---|---|---|---|
| Idle | No foreground Prometheus work; optional timeout | Unload unnecessary models, stop polling, batch low-priority indexing, keep minimal event listener and journal | Foreground input, scheduled authorized task or device event |
| Balanced | Normal default, modest resource headroom | Favor small models, interactive latency, limited concurrency, on-demand heavier model; postpone resource-heavy maintenance | User performance demand, lower battery, memory/thermal events |
| Performance | User chooses or policy identifies approved demanding workload with sufficient power/headroom | Load stronger model on demand, increase concurrency only as measured safe, prioritize active request and keep reserved OS/RAM headroom | Battery/thermal/memory protection, task complete, manual override |
| Battery Saver | Battery crosses user-set threshold or manually selected | Smaller models, EcoQoS for PARM-owned background work, longer polling interval, batch backups if safe, avoid unnecessary network and sensor wakeups | Charge status/threshold hysteresis and user approval |
| Critical Recovery | Severe low-memory warning, failed heartbeat, thermal distress, disk exhaustion, shutdown risk, corrupted queue | Stop accepting optional jobs, checkpoint or cancel safely, unload owned models, preserve logs/audit, recover queue idempotently, request intervention | Confirm stable health checks, successful integrity validation and controlled gradual resume |

### Expanded supplementary states (not replacements for five modes)
- **Deep Idle**: optional substate when no listening or jobs permitted; unmount/close idle resources where safe; wake on explicit request.
- **Quiet Hours**: user-defined no-noise/no-intensive-job time window; critical backups/security tasks as approved exceptions.
- **On-Battery Performance Guard**: limit peak compute if unplugged even when Performance requested, unless explicitly overridden.
- **Thermal Protection**: honor manufacturer OS thermal signals and reduce PARM-owned work before throttling.
- **Offline Continuity**: maintain local core tasks; queue external research until network available; no surprise sync.
- **Multi-device Cooperation**: move authorized, encrypted, idempotent tasks only after resource advertisement and local-device consent; never commandeer another device.

### Signal engine and scheduling design
1. Prefer native event signals over aggressive polling. Add **Windows CreateMemoryResourceNotification** (https://learn.microsoft.com/en-us/windows/desktop/api/memoryapi/nf-memoryapi-creatememoryresourcenotification) as a high/low physical-memory trigger; it is qualitative and should be combined with actual commit pressure and available memory counters.
2. Add **Windows Application Recovery and Restart** (https://learn.microsoft.com/en-us/windows/win32/recovery/using-application-recovery-and-restart) as a candidate for Windows-native restart callbacks, supplemented by application-level atomic checkpoints and verified restart state; it is not an unconditional guarantee of restart.
3. Evaluate Windows 11 built-in **Power & battery / Energy saver** (https://support.microsoft.com/en-us/windows/experience/power-battery/power-settings-in-windows-11) rather than forcibly changing undocumented system services. User OS power preferences take precedence.
4. Consider a **resource lease scheduler**: each model, process, tool and remote task requests finite RAM/CPU/IO/battery budget and releases it on completion; backpressure instead of overload.
5. Add **fair-share work queues** with task priority, aging to prevent starvation, soft concurrency limits, circuit breakers, cancellation, and persisted retry with idempotency keys.
6. Model-selection policy uses measured latency, quality tests, working set, quantization compatibility and recent failures; never merely presume bigger model is better.
7. Integrate optional resource-dashboard indicators: active mode, power source, reserved headroom, current models, queued jobs, reasons for throttling and last recovery event.
8. Implement policy simulation first; apply limits only to Prometheus-owned workers and explicitly approved user processes. OS-critical services and third-party foreground work are untouched.

### Initial proposed thresholds (illustrative, NOT production defaults)
- Battery Saver example trigger 30% unplugged, recover at 40% or AC; calibrate against actual battery health.
- Begin memory pressure mitigation on Windows low-memory event or repeated insufficient headroom readings, not on one transient sample.
- Keep reserved RAM for Windows and other programs, but choose precise headroom only after Dell benchmarking.
- Require persistence and cooldown before scaling back up after Critical Recovery.
- Never indiscriminately free RAM by clearing filesystem caches, forcing memory trims or killing essential processes.

### Additional explicit evaluation
- psutil system probes versus native Windows event callbacks: compare idle CPU use.
- Windows power savings are OS/hardware dependent; benchmark wall power or battery drain rather than assume percentages.
- Verify behavior when remote Dell is disconnected; local device retains autonomous safety veto.
- Validate persistent task journal, warm restart, recovery and no double execution of privileged steps.

**All of the above is a plan, not an implementation.**


## Predictive Resource Scheduler — approved for future planning (2026-10-14)
Status: documentation ONLY. Do not install or activate until the user explicitly authorizes implementation on the connected Dell.

### Goals
- Estimate **before starting each task** the expected peak RAM, CPU seconds, GPU/VRAM usage, disk I/O, battery drain, network needs and completion urgency, using measured historical per-task performance and current pressure.
- Choose *run locally now*, *use a smaller model*, *run a limited-size batch*, *defer and checkpoint*, or *offer a task to another authorized device*; remote execution requires explicit scope/permission and encrypted transfer.
- Preserve five owner-selected modes: Idle, Balanced, Performance, Battery Saver, Critical Recovery. Predictions inform each mode but cannot override Critical Recovery safety policy.
- Resource allocations use admission control, priorities, fair-share queues, leases, thermal and power constraints, hysteresis, task cancellations and release on completion.

### Prediction design
1. Begin with deterministic conservative cost estimates (model size/quantization/context length, input size, process startup, prior peak measurements). **Do not run an LLM constantly just to predict load.**
2. Maintain local per-device anonymous metrics history with retention bounds and provenance. Separate workspaces/family permissions; no sensitive prompt contents in performance logs.
3. Score task options against hard safety budgets and soft response-time goals. Low-confidence estimates get extra RAM headroom or a cheap calibration run; never gamble with OS health.
4. Reforecast during long work. If pressure rises: throttle optional workers, checkpoint recoverable operations, reduce concurrency or request operator guidance.
5. Before cross-device delegation, verify device identity, owner consent, battery threshold, availability, network data restrictions, and safe fallback; prevent duplicated irreversible actions on retry.
6. Benchmark prediction error, admitted peak RAM, swap/page faults, battery drain, fairness, task latency, recovery success, idle overhead and false throttling. Avoid unverified percentage improvement claims.

### Potential later libraries and platform features
- psutil for portable system measurements; Windows Job Objects and EcoQoS for Prometheus-owned process control, and Windows low-memory notifications for event-driven pressure detection.
- Ollama keep_alive and llama.cpp model residency for memory release.
- APScheduler for light scheduled jobs; SQLite/WAL journal or existing reliable Prometheus task journal for on-device restart.
- Evaluate time-series predictor only after enough benchmark data accumulates; avoid heavy always-on ML or Ray stack on a 16 GB laptop.

## CodeRabbit external reviewer candidate (2026-10-14)
- Product: https://www.coderabbit.ai/ ; GitHub Marketplace: https://github.com/marketplace/coderabbitai ; terms/pricing: https://www.coderabbit.ai/pricing ; FAQ: https://www.coderabbit.ai/faq
- Category: **external AI PR reviewer**, not a local resource manager and not an OSS self-hosted drop-in. Proprietary hosted service; enterprise self-hosting is a separate offering.
- Benefits: automatic PR summaries, code review comments, potential bug/security/maintainability suggestions, agent workflows and code-style rules. Complements CodeQL, tests, zizmor and dependency scanners; never use it as sole approval or quality gate.
- Cost: official site offers free reviews for qualifying public OSS repositories; paid plans and usage-based options exist, and eligibility/fair-use should be checked before connecting.
- Security gates: examine GitHub App requested permissions, vendor retention/training/caching controls, remote code-processing boundary and secret scanning; do not provide encryption keys, .env files, local device access or privileged automation rights.
- RAM: if using GitHub-hosted reviews, little continuous load on Dell; online dependence is a trade-off.
- Proposed usage after authorization: manual PR review on feature branches, advisory findings, human verification and tests before merge. No automatic code changes or merges and no enrollment now.
