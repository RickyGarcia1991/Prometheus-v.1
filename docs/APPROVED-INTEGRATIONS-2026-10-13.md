# Prometheus dev20

This edition adds executable AgentMemory and Browser Use adapters behind the
owner's existing text, voice and button approval flow. Open Owner → Documents,
code & connected computers → Integrations to inspect and use them.

AgentMemory can remember, recall and forget notes in a named project. It uses
the pinned standalone MCP package and a separate local JSON store per project.
It does not copy conversation history automatically. Notes travel with the SSD
under Interface/Data/Integrations/AgentMemory and are included in supported
data backups. The full embedding service and capture hooks are not enabled.

Browser Use opens a temporary browser to read a public HTTPS page, including
same-host JavaScript assets, then closes it. Returned links can be selected for
another read. The qualified operation is public-page reading; autonomous account
actions and a model-driven browser agent remain unqualified. Its temporary
profile stays on the computer. Its pinned package and browser stay on the SSD.

## Owner approval

A proposed action explains what it will do, why, what resources it uses and its
effects. Say or type `yes, approve`, choose the exact approval button, or reject
it. Approval is bound to the current owner, browser and proposal conversation.
The pending review expires after ten minutes; this is separate from the duration
of access you approve.

For ongoing work, describe the task, select its operations and resource scope,
and choose a duration. **Until revoked** has no routine expiration. A browser
task can cover selected hosts or explicitly allow any public website. Prometheus
chooses resources inside that scope; extending it produces another review.
Use **Use in this conversation** to resume existing task access after a restart
or in another conversation. The task's name stays visible. **Revoke task access**
ends continuing permission and asks a running action using it to stop. Stop
cannot undo an operation that already finished.

Examples you can type or speak:

- `Remember in project workshop: The reference drawings are in the blue folder.`
- `Recall from project workshop: reference drawings`
- `Use browser to read https://example.com/`
- `Give AgentMemory full access to project workshop until I revoke it`
- `Give Browser Use full access to all public websites for 30 minutes to research portable tools`

Full task access still describes a concrete task, supported operations and a
resource envelope. It does not install more tools or create provider accounts.
Grants stay with their issuing host and enrolled owner. Changing the application
policy requires fresh review. No hidden owner logger is included.

## Voice changes

Established speech ends after about 0.8 seconds of detected silence. Tentative
speech gets 1.2 seconds; a detected hesitation can extend later pauses up to
1.6 seconds. Manual Stop recording and End voice chat remain available.

Eligible plain chat begins speaking a stable sentence while the remaining
answer is generated. Structured answers and evidence-backed responses wait for
completion. A retry or interruption stops outdated audio. Listening resumes only
after the accepted answer and its audio finish. The voice model remains local
Whisper; no claim of identical ChatGPT latency or semantic turn detection is made.

Runtime qualification targets the existing portable Windows Python 3.12 setup.
The resource paths are relative to the SSD; physical operation on Ubuntu has
not been tested in this edition. Other researched repositories keep their
individual reference/setup status in the resource catalogue.
