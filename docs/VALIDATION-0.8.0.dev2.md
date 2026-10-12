# Validation: Prometheus 0.8.0.dev2 — October 10, 2026

## Final automated checks

- Python 3.12.14 development environment, pytest 9.1.1: **873 passed, 3 skipped in 152.56s (0:02:32)**.
- All four Node UI test scripts passed: app lifecycle/evidence/image controls,
  workbench behaviors, 7 remote-interface tests and 11 transport tests.
- Git whitespace/diff check passed before packaging.
- Three skipped checks require Windows symbolic-link/junction privileges. Other
  path/reparse regression checks still ran; physical cross-PC portability was not tested.
- The full suite exercised actual local HTTP streaming, cancellation, origin/token
  protection, recovery integrity, source editing/undo and package validation.
- Package-specific tests include real PowerShell validation/refusal checks. Actual
  signing and signed SSD activation were not performed: this PC has no original key.

## Measured local behavior

Live requests used a separate preview database, synthetic notes and a generated
128x128 two-color image. No production conversation database was copied or edited.

| Check | Observed result |
|---|---|
| Exact calculation | 37*19 = 703; 0.062 s |
| First evidence answer, Qwen3:1.7b | Correct 17-minute answer with S1 citation; 6.953 s |
| Warm follow-up, same model | Correct cedar answer with S2 citation; 3.750 s; model reload 0.012 s |
| Warm residency | Ollama expiry was five minutes after the request |
| Small image, Qwen3-VL:2b | Correct blue/red identification; 46.297 s; image not saved |
| Document import after separating indexing | 0.125 s; keyword-searchable without model loading |
| Evidence after switching back from vision | Correct cedar answer and version provenance; 41.594 s, including 37.609 s reload |
| Larger 1586x992 image | Exceeded 120-second limit; no completed answer saved |

Model switching and cold starts can be slow. The small visual fixture does not
establish OCR, detailed image reasoning or general vision accuracy. Timeout cleanup
and guidance were subsequently covered by regression tests. Test notes/citations
do not establish accuracy on the user's own documents or professional questions.

## Issues resolved during validation

Grounding budget exhaustion now returns excerpts; source IDs avoid collisions
with supplied history; provenance and truncation flags survive context packing.
Read-only document retrieval cannot migrate/write its database. A clipped context
does not replace a longer literal matching excerpt. Default source replacement
requires a selected development workspace with protection and undo.

Windows test children now discover the correct PowerShell modules; temporary
storage is scoped to the main-PC work directory. The launcher fixture preserves
virtual-environment metadata when testing paths with spaces. A cancellation test
observer now accepts both EOF and actual connection reset/abort as disconnection;
its timing and required socket-closure assertions were not relaxed.
Rejected HTTP requests now send their refusal before discarding a bounded amount
of arriving data and closing. This fixes the Windows response-loss race without
dispatching unauthorized actions or trusting malformed body framing.

## Unverified or conditional

No new remote-account pairing, smart-home action, microphone recording, weight
training, large-model load, physical drive eject/reconnect, source-signing or SSD
activation was performed in this upgrade. Installed features are not automatically
configured accounts or verified hardware capabilities. Existing signed core,
controller, resources and SSD user data were preserved.
