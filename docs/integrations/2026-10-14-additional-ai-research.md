# Additional AI and tools research — 2026-10-14

Status: **research and planning only**. No downloads, installs, runtime configuration, integrations, testing on Dell, or changes to main authorized.

These are **candidate references, not confirmed net-new unique groups**: compare against the authoritative 79 group inventory and the Dell local integration state before marking new.

## High priority
| Candidate | Source | Purpose | Known constraints |
|---|---|---|---|
| ONNX Runtime GenAI | https://github.com/microsoft/onnxruntime-genai | Windows CPU/DirectML/OpenVINO inference and constrained decoding; compare with Ollama | Validate model formats, quantization, footprint and Windows API compatibility |
| sherpa-onnx | https://github.com/k2-fsa/sherpa-onnx | Offline speech recognition, synthesis, speaker identification, VAD, wake words | Apache-2.0; Windows x64 supported; per-model licenses vary |
| Microsoft MarkItDown | https://github.com/microsoft/markitdown | Convert documents to Markdown for local knowledge ingestion | Sanitize and sandbox hostile documents; dependency/format behavior testing |
| pywinauto | https://github.com/pywinauto/pywinauto | Windows accessible GUI control | BSD-3-Clause; UIA/Win32 limitations, session/elevation and approvals |
| Promptfoo | https://github.com/promptfoo/promptfoo | Regression evaluation and prompt-injection tests | Local eval configs can execute code; isolated test environment; verify current license per components |

## Conditional/later
| Candidate | Source | Purpose | Constraints |
|---|---|---|---|
| llama.cpp | https://github.com/ggml-org/llama.cpp | Local model execution / GGUF baseline | MIT; check whether existing Ollama already covers need |
| BoundFlow | https://github.com/boundflow/boundflow | Durable agent approval and audit concepts | Verify maturity, license and dependency cost before coding |
| GPT Researcher | https://github.com/assafelovic/gpt-researcher | Multi-source web research | Apache-2.0; network/privacy/API spend controls |
| Langfuse | https://github.com/langfuse/langfuse | Traces and evaluations | OSS MIT except EE components; resource intensive self-hosting; telemetry review |
| Windows ML | https://github.com/microsoft/WindowsML | Windows-native AI hardware acceleration | Validate Dell OS and available acceleration first |

## Requirements for future review
- Compare candidates and their functions against the original 79 candidate groups, previous discovery notes, and code already on Dell/SSD. Do not increment unique count without evidence.
- Track upstream commit, exact package and model licenses, version pin, hashes/signatures, dependency tree, CVEs, compatibility and rollback.
- Benchmark on actual Dell hardware using 16 GB RAM cap, CPU/GPU utilization, latency, startup delay, resident memory, idle energy and disk growth.
- Preserve single backend/service where possible; prefer shared ONNX runtime and on-demand workers rather than permanently resident parallel AI stacks.
- All environmental sensing, microphone, desktop control, web research and autonomous actions go through explicit scope/privacy boundaries and action approvals.
- No production adoption or activation until user's explicit later authorization once Dell is connected.

Research references: verified official repository descriptions on 2026-10-14; security and practical integration suitability are *not* fully audited.
