# Research integration — October 8, 2026

Development version: 0.8.0.dev1. Baseline: caeec01 on fix/controller-alignment-safe-eject.
Work branch: feat/research-memory-integration-20261008.

The existing research route switched agent-mode questions to plain-model compatibility chat.
This change registers already-retrieved source evidence as a local read-only tool, requires it in
agent execution, preserves it across retry/approval resume, and retains the agent's evaluation
and conversation-save behavior. The evidence reader itself performs no network request.
Network retrieval remains gated by --online-research and the configured HTTPS JSON endpoint.

Research is not inserted into trusted knowledge. --retain-research stores bounded successful
contexts in research_notes with session ownership, a context hash, and a UTC timestamp.
Source contexts also contain URLs, retrieval times, and hashes of the source body supplied by
the provider. Cache retrieval ranks query-token matches from the 200 newest notes; default agent
context limits each cached excerpt to 1200 characters. Explicit cache recall returns stored evidence
with an unverified label and performs no model/tool planning. General questions may use matching
cache evidence as context, subject to the small model's limitations. Compatibility mode does not
recall the research cache. Source and context hashes record identity; they do not establish truth.

The real-model smoke initially exposed unrelated hardware-tool selection during cached recall.
The deterministic explicit-recall path fixes that observed case. A fresh retest with local llama3.2:1b
answered the labeled Atlas electric-wrist fixture from fresh evidence in 8.32 seconds, attached its
source URL, then recalled the same persisted excerpt after reopening the database in under 0.01 seconds.
SQLite integrity was ok and trusted knowledge remained empty. This is a fixture smoke, not online
research validation or a general intelligence benchmark.

Baseline verification: 205 tests passed in 63.89 seconds with an explicit checkout source path.
Targeted final regression checks: 51 tests passed, including research boundaries, opt-in retention,
chat/ask integration, bounded persistence/recall, empty evidence, failure behavior, visible URLs,
and deterministic cache recall. The complete final suite result is added below after completion.
The PowerShell deployment script parsed without errors. Existing controller binaries are reused;
this increment does not claim controller lifecycle or physical eject/reinsertion validation.

No production user history was used by the smoke tests. There is no model training, automatic
promotion of research to facts, live-provider setup, or performance claim beyond the recorded fixture.

Final complete suite: 218 tests passed in 62.38 seconds using py -3 -m pytest -q with no
manual PYTHONPATH override. This includes all 13 new research/agent/cache regressions.
