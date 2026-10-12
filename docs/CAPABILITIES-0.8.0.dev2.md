# Capability upgrade 0.8.0.dev2

| Workflow | Improvement | Requirements / limit |
|---|---|---|
| Ask with evidence | Answer from documents, projects or offline collections; preserve sources | Local model/RAM for synthesis; excerpts remain available without inference |
| Image questions | Explicit upload, local streaming, cancel, no image storage | Installed vision model; PNG/JPEG <=8 MiB; recognition quality depends on model |
| Project search | Symbols, phrases, relevant words, adjacent context and exact citations | Bounded text scan; excludes known private/linked paths |
| Model routing | Catalog fallbacks, partial-check promotion guard, four cases per role, actual output budget | Small checks do not establish broad accuracy |
| Responsiveness | Five-minute warm residency when RAM permits, otherwise 30 seconds | Measured performance depends on host/model |
| Discovery | Capability button and protected schema/risk catalog | Discovery grants no execution rights |
| Source editing | Selected development root, protected installed code, hash checks and undo | Explicit user action required |
| Health | Short runtime-probe timeout instead of full generation wait | Missing/offline runtime reported promptly |
| Release | Deterministic unsigned candidate and original-signer handoff | Original signing identity and coordinated SSD activation still required |

Existing local conversation, exact math, code assistance, versioned document
library, optional meaning indexing, project checks, offline references, public
research, explicit watches, local voice, verified backup/recovery, remote access
and configured Home Assistant workflows remain under their existing conditions.
Installed, configured and live-tested are distinct states.

No model weights were trained. No paid service, account, private API or proprietary
plugin was copied. These changes need no automatic model download or new runtime
package. Workflow similarity does not establish ChatGPT-level answer quality.

See `ARCHITECTURE.md` for extension rules and `CAPABILITY-UPDATE.md` for signing,
activation and rollback. A separate execution report records this host's results.
