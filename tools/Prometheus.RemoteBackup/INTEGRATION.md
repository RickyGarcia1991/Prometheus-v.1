# Portable memory backup helpers

Prepared from an audit of the current host. The remote Dell installation has not been inspected or changed.

Copy these four reviewed files together into the current user's `%LOCALAPPDATA%\Prometheus` directory as a transaction, preserving prior versions for rollback:

- `host-memory-backup.ps1`
- `ssd-memory-backup.ps1`
- `Invoke-MemoryBackup.ps1`
- `scheduled-memory-backup.py`

The package can be included in the signed core under `tools/Prometheus.RemoteBackup`. It does not import core modules, require a developer checkout, modify the active release, migrate memory, or create scheduled tasks. It uses the attached SSD's `Prometheus-Resources\Python\python.exe` in isolated mode. All Python imports are from its standard library.

Existing lifecycle tasks can keep their current wrapper names and quiet-host entry points. The setup owner must decide whether the target computer already has those tasks and whether scheduling is desired. No tasks, credentials or live host files were changed in this staging work. Install these files after any host-agent installer that still copies older wrappers; a later older installer could otherwise reintroduce stale scripts.

Each run discovers exactly one `Prometheus-2TB` volume (removable or fixed USB presentation), derives absolute paths using its current drive letter, and skips without changing memory when that SSD, source database or stable eject state is unavailable. A missing runtime is a failure, not a silently successful backup.

Host backups go to `%LOCALAPPDATA%\Prometheus\backups\memory-automatic`; SSD backups go to `Prometheus-Recovery\Memory-Backups` on the discovered volume. Source memory is opened read-only and copied with SQLite's online backup API, which includes committed WAL data. The snapshot and a restored copy must pass integrity, relationship and supported-schema checks. Snapshot record counts are compared with the restore, not a racing live database. An OS lock serializes backup and retention work per destination; it does not prevent normal conversation writes.

Only new `prometheus-memory-auto-v2` snapshots with matching file name, size, SHA-256 and database record metadata count toward retention. Fourteen verified snapshots are retained by the wrappers; old-format, malformed, changed or linked files are left alone. Failed checks never trigger retention. A failed publication may leave an incomplete file without a valid manifest for investigation; it is never counted as a verified snapshot. Output includes counts and status, never conversation text.

Limits: two GiB per database, 120 seconds per run, at most 1,000 retention candidates. The helper does not authenticate portable Python or the volume by its label; installation and the SSD runtime remain part of the existing trust boundary. This is a readiness addition, not proof of a completed restore drill on the remote Dell or a new backup schedule.
