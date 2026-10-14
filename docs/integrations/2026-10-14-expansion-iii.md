# Prometheus resource expansion III — 2026-10-14

**Status: RESEARCH / FUTURE PLAN ONLY.** No installs, integration code, device access, production changes, or testing requested. The original 79 candidate-group inventory is not present in the current remote GitHub main branch; all candidates are provisional until deduplicated against the Dell/SSD workspace and prior archives.

## Verified upstream candidates and suitability
| Candidate | Primary source | License / caveat | Potential benefit | Dell 16 GB disposition |
|---|---|---|---|---|
| Graphiti | https://github.com/getzep/graphiti | Apache-2.0; database and LLM/embedding dependencies | Time-aware, provenance-tracked knowledge graphs | Defer server deployment; evaluate data model concepts and compare with SQLite memory |
| LiteLLM | https://github.com/BerriAI/litellm | MIT outside enterprise-specific portions; advanced router features may be commercial | Local/cloud model routing, fallback, usage policy | Explore library or lightweight routing; avoid always-running proxy and Redis |
| PaddleOCR | https://github.com/PaddlePaddle/PaddleOCR | Apache-2.0; verify model assets and Windows package compatibility | Read scans, labels, instrument panels, repair manuals | Evaluate on-demand CPU OCR with resource limits |
| Florence-2 Base | https://huggingface.co/microsoft/Florence-2-base | Check exact model card license and gated requirements before use | Compact image captioning/object detection/OCR-style vision tasks | Try quantized or CPU-friendly runtime only after resource benchmark |
| Temporal Python SDK | https://github.com/temporalio/sdk-python | MIT; Temporal server needed for full durable workflow architecture | Crash-safe workflows, resumable tasks, retries | Study design; prototype lightweight journal first |
| openWakeWord | https://github.com/dscripka/openWakeWord | Apache-2.0 code; bundled pretrained models CC BY-NC-SA 4.0 | Local always-listening wake-word detection | Potential low-load on-demand audio process; check model licensing |
| OCRmyPDF | https://github.com/ocrmypdf/OCRmyPDF | MPL-2.0; OCR engine and other native dependencies | Searchable scans and text retrieval | On-demand batch conversion, not resident service |

## Additional architecture research priorities (not separate vendor candidates)
1. Hardware-aware model scheduler selecting small/large model, CPU acceleration, and inference context based on free RAM, task urgency, latency and user priority; no unsafe OS process termination.
2. Source-provenance graph with contradiction resolution, temporal validity, per-family profile isolation, and deletion propagation.
3. Work journal with idempotent action IDs, task checkpoints, at-most-once privileged operations and recovery after restart.
4. Multimodal permission broker for microphones, cameras, file watchers, sensors and desktop automation; explicit consent and access logs.
5. Resource budget governor: working-set, commit charge, CPU, IO, battery/thermal load, foreground application priorities, and optional worker unload.
6. Offline-first research queue with verified fetch provenance, change alerts, cache expiry, and malicious-document/prompt-injection filtering.
7. Security test harness for restore validation, authorization bypass attempts, malicious prompts, software supply-chain integrity and audit trails.

## Required acceptance gates before future integration
- Identify duplicates against all 79 original groups and existing Dell development; record a canonical resource identifier.
- Verify source origin, license by exact version and model artifact, active maintenance, hash/signature and vulnerabilities.
- Establish hardware baseline on Dell 16 GB; test idle RAM and peak RAM, disk, battery, cold start, CPU usage.
- Select a single implementation per function unless justified; default new services to off.
- Keep owner approvals for privileged actions, prevent uncontrolled network access, save baseline snapshot and practice rollback.
- Do not merge this planning branch or install anything without a separate later authorization.

Reviewed public primary upstream information on October 14, 2026. **No field benchmark, security audit or full historical inventory reconciliation has been completed.**
