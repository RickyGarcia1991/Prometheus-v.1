# Prometheus Drive Controller — State & Progress Contract

The GUI is a presentation layer over verified runtime state. It must never infer "safe to eject" from elapsed time.

## Primary states
- DriveAbsent
- Ready
- Starting
- Running
- Stopping
- ReadyToEject
- Blocked
- Faulted

## Startup progress (100 points)
| Check | Weight | Completion evidence |
|---|---:|---|
| Prometheus SSD identity verified | 10 | Expected device/identity is mounted |
| Runtime files validated | 10 | Required launcher, Python, Ollama and model paths exist |
| Ollama API online | 15 | Local /api/tags responds |
| Requested model available/loaded | 20 | Model is present; active load confirmed when applicable |
| Memory database verified | 10 | SQLite quick/integrity check passes |
| Knowledge/resource stores verified | 10 | Required resource DB/files readable |
| Local index/resource initialization | 15 | Actual initializer reports completion |
| Chat endpoint/client ready | 10 | Prometheus chat runtime is accepting input |

Overall percent = sum of weights for completed checks plus measurable fractional work reported by an active check.
Never advance a check from a timer alone.

## Shutdown progress (100 points)
| Check | Weight | Completion evidence |
|---|---:|---|
| Graceful shutdown signal sent | 10 | shutdown.request written |
| Active chat clients closed | 20 | zero Prometheus chat clients |
| Session/database integrity verified | 20 | SQLite integrity checks return ok |
| AI/model processes stopped | 15 | owned Ollama/llama-server processes absent |
| SSD-backed process references cleared | 15 | no remaining D:/device-backed process refs |
| PnP/UASP removal checks clear | 20 | no current known removal blocker/veto evidence |

ReadyToEject requires all shutdown checks to pass. A failed check transitions to Blocked/Faulted and preserves the evidence.

## UI rules
- Green = verified safe/ready state.
- Yellow = active/running/in-progress state.
- Red = blocked, failed, or unsafe-to-eject state.
- Gray = unavailable/not yet evaluated.
- Individual progress rows show Pending, Running, Passed, or Failed.
- Percentages are derived from the checks above; fake/smoothed completion is prohibited.
