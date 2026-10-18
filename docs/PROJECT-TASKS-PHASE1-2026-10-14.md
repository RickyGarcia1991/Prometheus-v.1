# Prometheus phase one: reviewed project tasks and idle public research

Development release: 0.8.0.dev23. Installation requires the owner's separate approval.

Open an intended development folder under Code, then describe the change under
“Ask Prometheus to edit this project” or select “Edit selected project” in the composer.
Plain conversation also accepts “Edit the selected project: <instruction>”.

Ask first generates a proposed edit without writing. The review shows the exact
diff, reason, benefit, project and checks. Approve/allow applies it once; deny/reject
discards it. Use judgment asks for approval of the task first, then chooses one
existing file and an implementation, applies it, and runs the reviewed presets
without a second per-edit question. The task can change the implementation approach
within its approved scope. Stop cancels work; it is not permission to ignore denial.

Model output consists of exact unique replacement spans, not shell commands.
All changes use the existing workspace's stale-file hashes, atomic writes and
original-byte backups. Failed or cancelled checks trigger undo when no newer edit
exists. A conflict preserves newer content and identifies recovery needing attention.
Python receives a syntax check; automatic test discovery recognizes unittest,
pytest and node:test. A result without a supported test suite says so explicitly.
Tests execute as the current Windows user. The scope review describes that effect.

The local model router chooses a fitting installed helper and supplies bounded
source excerpts for larger files. The current editor changes one existing supported
UTF-8 file up to 256,000 bytes. Source/context and output are validated before writing.
Undo survives a browser/app restart: reopen the same project to restore a matching
undo choice. Newer file content is never overwritten by undo.

An optional explicit public query requires review before network dispatch. Project
files and arbitrary chat history are never automatically turned into search queries.

Maintenance → Research while idle lets the owner approve a public topic and interval
(15–1440 minutes). While running and idle, the scheduler searches only that topic,
stores bounded excerpts with citations/hashes and retains changed observations.
Relevant saved evidence grounds later chat answers. It does not train model weights.
Closing a browser tab keeps an enabled research session running; Close Prometheus
stops the process. The saved preference resumes when Prometheus is next launched.
The scheduler does not install an OS startup task. Disable revokes future research
while retaining findings. Cancel stops an active job; then disable prevents recurrence.

Storage is bounded to 200 observations and 50 run records. Research state and edit
backups are inside existing backup-covered Maintenance/Coding directories. Pending
action approvals remain session-bound and expire after ten minutes or app restart.

Later phases remain separate: multi-file/application self-improvement, unattended
repair/startup/resume, and broader model/agent qualification. This release preserves
the existing signed-installation and owner-access workflows.
