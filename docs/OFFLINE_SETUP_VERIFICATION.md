# Offline chat setup verification — 2026-10-04 UTC

## Delivered milestone
Local-only Prometheus text chat with saved conversations and explicit resume. Windows source and Desktop launchers. Original foundation preserved on main; new code on feature-offline-chat. No external Git push.

## Environment
Windows 11 Home; approximately 8 GB RAM. Python 3.14.8. Node 24.21.0 and npm 11.19.0 work from the saved user PATH. Ollama 0.35.1 and local llama3.2:1b. The existing large model is retained, not loaded. Approximately 9.62 GiB free disk at the measured checkpoint.

## Evidence
- Original baseline: 8 tests passed; pre-change archive verified.
- TDD: 21 assistant failures before implementation; launcher failure before implementation.
- Complete tests: 41 passed in 39.90 seconds under coverage; assistant branch-aware coverage 98%.
- Restored candidate source: 41 passed in 29.91 seconds.
- Compileall, staged diff whitespace review, and ai-devkit feature lint: passed (16 lint checks).
- Real synthetic chat: marigold repeated in 6.19 seconds.
- Separate-process selected-session resume: marigold recalled in 0.53 seconds.
- Saved synthetic history: 4 turns; 2 prior context turns submitted.
- Doctor confirmed installed local model, writable default history, and cloud_fallback=false.
- Desktop launcher --help passed. A separate normal CMD chat process was started and remained running. Native UI was not visually inspected.

## Recovery evidence
Source candidate commit: 6789d04.
Verified candidate: archives/prometheus-offline-chat-20261004T040007605260Z.zip and its .sha256.
The candidate was exported from tracked Git files, then checksummed and manifested by the original Control Center. No SQLite chats, virtualenv, model weights, runtime files, or coverage data were included. .coveragerc is intentional source configuration.
Baseline: archives/before-offline-chat-20261004T032305014459Z.zip. It reverified and restored doctor passed.
Both restore checks used new directories under local app-data validation, not overwrites.
After recording this report, the final documentation-inclusive archive is generated and independently verified during handoff. Production code and tests are unchanged from the tested candidate.

## Limitations and preserved boundaries
Plaintext history is not encrypted or automatically verified knowledge. Recent model context is bounded; full saved history is retained. This is not model retraining, autonomous tool execution, online research, source verification, voice, or semantic retrieval. The small local model can hallucinate.
No new cloud account, paid service, firewall/network change, PowerShell execution-policy change, deletion of user data/models, or public upload was performed.

## Diagnostics
The optional ai-devkit task command was unavailable; Markdown planning was used. A final ai-devkit npm download succeeded after a delay. The archive privacy check initially matched .coveragerc too broadly; corrected after inspection. A native launch attempt encountered an empty ComSpec variable; resolving installed cmd.exe with Get-Command succeeded. These checks did not require changes to the assistant production code.

## Operator
Keep Ollama running. Double-click Start_Prometheus_Offline.cmd on the Desktop and type a question at You>. Use /exit to leave.
The default plaintext memory database is %LOCALAPPDATA%\Prometheus\memory.sqlite3, outside source backups. Back it up separately when Prometheus is closed if you need to preserve chats. Source backups require Python/Ollama/model installations to be available separately.
