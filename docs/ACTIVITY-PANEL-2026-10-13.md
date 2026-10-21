# Visible work activity

Prometheus dev22 adds an Activity panel above the message composer, visible in
both the conversation and workbench views. It shows actual task stages, elapsed
seconds, selected code filenames, outcomes, and background source/update checks.
Expand or collapse Activity to choose how much of the recent history is visible.

Code suggestions and previews explicitly say that no files have changed. Applied
edits, failed or cancelled checks, and reviews have distinct outcomes. The Code
workbench continues to show the exact diff and test output. No approval or file
write is triggered by the Activity panel.

The server retains at most 12 tasks and 24 stages per task in memory; the panel
shows the latest six tasks and four stages each. Reloading the page retains this
history while the server is running. Restarting Prometheus starts a new history.
Activity does not retain prompts, generated answers, source contents or raw error
payloads. The existing owner sign-in protects its status and job endpoints.
Disconnected views show the last known state instead of claiming current work.

This panel reports jobs performed by Prometheus. Work performed by a separate
Codex chat is described in that chat's progress updates. Remote Desktop Commander
actions retain their existing status in Connected computers.

This change adds no model, integration, voice setting, cloud call or dependency.
The full Browser Use agent and OpenViking remain unqualified. Dev21 is the signed
rollback baseline. Release receipts identify the exact source files and checks;
the source checkout may contain uncommitted activity-panel changes.
