# Backup maintenance hooks: bounded reviewed preparation

Read-only source review only. No stop, marker write, task change, eject, or production restart was performed. This does not make full backup executable.

The installed dev55 `END_PROMETHEUS.cmd:4` selects the owner-profile `Prometheus/active-chat.shutdown` marker; line7 writes it, lines10–17 wait about100seconds and refuse on timeout. `START_PROMETHEUS.cmd:29` selects the same marker; its chat branch removes it before starting and after the application exits. Marker deletion alone is not sufficient exit evidence.

`ui_server.py:1349` observes the shutdown marker, stops accepting further interactive work, waits until the current operation is absent, then calls `app.close()` and `server.server_close()` in the finally block (lines1358–1360). This is the existing graceful application shutdown protocol to reuse, with retained exact chat-process handles and bounded native exit observation.

`Prometheus-Drive-Engine.ps1:35` combines that signal with Studio shutdown, SQLite integrity verification, model stopping, and a drive-process scan. `Prometheus-Controller-Common.ps1:55` provides Studio's localhost bootstrap/token/close protocol. The drive engine's model stop uses rechecked PIDs, and its larger workflow has state/marker side effects. Do not invoke the entire engine as the backup-maintenance owner. The eject orchestrator's PrepareOnly path is also not a drop-in backup contract: it changes desired-running and remote-runner markers and is designed around one removable-drive eject lifecycle.

Minimal missing executable adapter: capture exact original task/process/restart state; retain the existing cooperative lifecycle locks and suppress automatic restart; create only a transaction-owned shutdown signal and await the captured application exit; verify portable SQLite after exit; use individually qualified cooperative hooks or retained exact process handles for the known model/controller writers; refuse unknown or surviving writers. Preserve exact original marker contents and never overwrite another maintenance owner's markers. Bind an authenticated maintenance receipt to source/destination identities, owner/session and transaction, revalidate it and writer absence each batch, and restore only the components originally running through pinned existing startup recipes. Migration locks alone do not establish this quiescence.

The existing inspected source-selection rules use drive paths/commands, not exhaustive filesystem write-handle ownership. The supported contract remains cooperative maintenance with observed known writers stopped; it does not prove absence of arbitrary unrelated writers. No generic process-name kill or physical eject is part of the proposed adapter.

## Inspected source pins

| File | SHA-256 |
|---|---|
| END_PROMETHEUS.cmd | `75f0377436e6836b15bf606202419928d2bb239e0143980666a35527079bf5f9` |
| START_PROMETHEUS.cmd | `4bf3d302b67cc92461dbad7fb2bdcab7b76ed384bb78f0838628dcf3de303453` |
| ui_server.py | `7559789a6d47e702441e15135963f53ed0428a147e5d4a9a3b84436c46700b18` |
| Prometheus-Drive-Engine.ps1 | `0cd57506beda5c6b289a45392bbc3104fde568a222e3b43fab91a775cdfc4a3e` |
| Prometheus-Controller-Common.ps1 | `739be505bf32c87410c6e29058599046e906b4bf37acc45f2c3d8bf32af25c75` |
| Prometheus-Eject-Orchestrator.ps1 | `a7eb2b92485f9ad991040e076a934ff3d4cb291c7a08ac449469946592dad19a` |
| Prometheus-Lifecycle.ps1 | `eabb090363668d758f9409d7abc12cc0f365bb9077d1b306519ff4b425b739a0` |
