# Ubuntu resources and remaining plans — 26 October 2026

This is an implementation and evidence record, not a claim that every proposal is installed or that security analysis passed. The native Surface deployment uses Ubuntu 26.04.1 amd64, Python 3.14 and Node 22.22.1. Existing Windows release branches and recovery archives remain separate.

## Implemented in this checkpoint

| Capability | Native Ubuntu behavior | Evidence and limits |
| --- | --- | --- |
| Voice cue | Visible preparation, ready-to-speak, finishing and transcription states | 14 browser state assertions at desktop and narrow sizes. Microphone status was synthetic; no owner speech recorded. |
| Resource discovery | Shared HDD references are connected separately from the active Ubuntu model server | Regression checks ensure Windows executables and stored Windows model names are not advertised as native tools. |
| Offline archives | Pinned native libzim, libxapian and Python extension | All 38 archives opened and one entry per archive decoded. Three real archive-worker queries returned results. This does not validate every article. |
| Reference databases | Existing public, medical, mathematics, engineering and OpenAI reference readers | Five real queries returned five results each. File integrity is recorded separately in the full resource audit. |
| AgentMemory notes | Existing pinned JS note adapter uses OS-managed Node on Linux | Disposable save, recall and forget succeeded. This does not qualify the full Windows-only Agent Memory service. Node permissions are not an OS sandbox against malicious code. |
| Document import | Native bounded PDF, DOCX, XLSX and PPTX text extraction | Real extraction and malformed-input tests. No OCR, macro execution, layout rendering or formula calculation. |
| Semantic retrieval | Local qwen3-embedding:0.6b through the existing document library | Four synthetic retrieval cases matched expected documents. No broad accuracy claim; indexing remains resource-gated. |
| Public web reading | Fresh native Chromium reader with parent-side public HTTPS requests | Real public page read; native script fixture, scope filtering and cancellation tests. No sign-in, form submission, downloads or interactive agent qualification. |
| Native health | Native deployment metadata, listed-file hashes, memory structure, native runtime and loopback model inventory | Passive checks explicitly leave physical and model acceptance untested. No inference, scan or release-signature claim. |
| Automotive reference foundation | Manufacturer PDF converted and searched locally | Avery Dennison cable-tie catalog: 3,068,944 bytes, SHA-256 1c6dcd402ebaf11908ef9e940202f79e1924e40bd5d8f74ae5d8df60a38a34a9, 53 extracted passages. Retrieved 26 October 2026; publication date not established. |

The new embedding model's observed digest is ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d. Its weights live in the native Ollama store, not the source repository. The manufacturer PDF is local reference material and is not committed here.

## Reconciled plans

The saved integration expansion v12, consolidated vision, work queue, candidate research plans, automotive next-step plan and OpenCode handoff include proposals as well as completed work. A catalog entry, installed package, resource-fit estimate and executed acceptance test are different evidence.

| Plan area | Current state | Remaining work or exact blocker |
| --- | --- | --- |
| Voice, camera, image understanding | Native paths implemented; earlier camera capture and local image task passed | Owner speech accuracy and listening confirmation remain outstanding. Synthetic loopback recognition had errors. |
| Connected computers | Native pinned client can launch | Prometheus's own OAuth initialization timed out; no token was enrolled. Its device listing, reviewed action and result on the Windows computer remain unverified. External Desktop Commander access does not satisfy this. |
| Administrator execution | Exact local terminal approval and Ubuntu authentication flow exists | Root-identity test was declined. No successful root execution claimed; declined action was not reissued. |
| Public browser capability | Native read-only equivalent implemented | Interactive model-driven browsing remains unqualified on both platforms. |
| Agent frameworks and coding integrations | Candidate catalog and selected adapters exist | Full Windows Agent Memory service, external coding-agent accounts and other agents require separate setup and bounded acceptance. |
| Resource scheduling | Admission checks, history and saved preference exist | OS quotas, predictive scheduling and automatic multi-device placement are incomplete. |
| Verification council | Deterministic review records and sequential local advisers exist | Independent reviewer quality and broad benchmark coverage are not established. Aggregation is not proof a check ran. |
| Discovery Engine / continuous research | Catalogs, source watches and selected idle research exist | Full large-scale discovery, autonomous trial/reconciliation and reliable quality scoring remain future implementation. |
| Model development | Local models and lifecycle controls exist | Automatic training, continual learning and model-weight self-improvement are not deployed. |
| Ubuntu release promotion | Reviewed manual native checkpoints | Autonomous signed Ubuntu promotion is not implemented. Preserve accepted Windows promotion/recovery artifacts. |
| Family profiles / adaptive personality | Saved vision | Not qualified as deployed. Needs explicit profile, privacy and acceptance design. |
| Distributed architecture / robotics | Saved research plans | Not implemented as a coordinated runtime. Hardware catalogs are references. |
| Security tool catalog | 814 package entries and research candidates | Not 814 installed or validated security tools. OpenCode remains on hold pending isolated audit; no blind installation of candidate scanners. |
| Automotive workflow | PDF import and sourced retrieval foundation | Vehicle-specific PDI flow, complete approved resource registry and update policy still need implementation. |
| Financial / external-account features | Saved ideas and selected interfaces | No live trading or account enrollment performed. Provider authorization and task acceptance remain prerequisites. |
| CodeQL | Separate workflow verification task | YAML validity, functional tests and publication do not prove a Python scan, SARIF ingestion or absence of vulnerabilities. |

## Evidence locations and recovery

On the Surface, reports/resource-audit-20261026 contains regression output, browser checks, archive sample/query results, reference queries, semantic retrieval, connection attempt, and the full checksum progress/final record. The full integrity audit covers 119 unique files totaling 159,192,488,683 bytes; completion and individual results must be read from its final record, not inferred from this scope statement.

Earlier voice, camera, image and interface evidence remains in reports/voice-admin-sync-20261026 and reports/ubuntu-completion-20261026. It does not automatically apply to future modifications.

Native checkpoints exclude owner passkeys, backup PIN state, OAuth tokens and private conversation data. A runtime/source checkpoint is not a bootable HDD image, an executed restore test or a copy of native Ollama model weights. Keep the HDD connected while Ubuntu runs from it.
