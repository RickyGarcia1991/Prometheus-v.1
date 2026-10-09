# Source checks, backup and portable readiness

Source monitoring watches only public queries that the user explicitly selects.
The interface must display the query and publisher when enabling a watch. Enabling
a watch authorizes repeating that displayed query at its chosen interval while
Prometheus is open. Chats, local documents and coding prompts are never picked up
as source queries. There is no independent background service or cloud scheduler.

`FreshnessSchedule(state_path, history_root)` stores due dates, errors and leases
in SQLite. `watch(provider, query, interval_days=7, enabled=True)` adds or updates
a watch. `status()` is offline. `run_due_checks(online=True, max_checks=1)` performs
one due query by default, with the existing bounded public publisher connector.
The caller runs this between foreground jobs on the single app worker. A persisted
five-minute lease prevents duplicate simultaneous queries from different instances;
an interrupted lease expires. Failures retry no earlier than six hours later.
Disabled watches and `online=False` make no network calls. At most three checks
can be requested per call and 100 watches can be stored.

All returned snapshots go through the append-only, checksum-verified public
history store. Source result changes are compared independently of retrieval time.
The first check establishes a baseline. A successful watch says that query was
checked; it does not certify every source, legal rule or filing deadline is current.

`create_backup(memory_path, destination_root, data_dirs={...})` creates a new
timestamped backup, verifies every SHA-256 and publishes it atomically. By default
the destination must be on a separate volume from conversation memory. This is a
volume check, not proof that partitions are on different physical hardware. The
caller should choose a host-local folder when Prometheus data lives on the SSD.
Supported extra data labels are `Library`, `Coding`, `Maintenance`, `PublicHistory`
and `Routing`. Missing selected folders are ignored. Models, credentials by known
filenames, key files, caches and live SQLite sidecars are excluded. SQLite files
use the online backup API so committed WAL contents are included consistently.
The backup must run between other app jobs to keep related files consistent.

Backups are plaintext and should be kept in a private folder. Filename exclusions
do not detect secrets embedded inside ordinary documents or source code. This is
a bounded user-data backup, not a full SSD clone: 10,000 files, 64 MiB per file and
512 MiB total. An oversized backup fails explicitly; it does not silently discard
large selected documents. Old successful backups are never overwritten or pruned.

`verify_backup(folder)` rechecks hashes. `restore_backup(folder, destination)`
verifies before and after copying into a **new** destination, suitable for a restore
drill. It refuses an existing target and cannot overwrite live data. Root/UI code
may record the returned verified path and counts. A successful separate staging
restore is stronger evidence than merely checking that a backup file exists.

`readiness_report(data_root, resources_root=None)` observes RAM, disk capacity,
Python/runtime presence and Windows audio-device counts. It opens no microphone
or camera and does not test eject/reconnect. Actual audio, physical SSD portability
and performance on other hardware remain explicit user-run checks.

Suggested maintenance: review changed watched sources weekly; verify a fresh
off-drive data backup after substantial work; perform a separate restore drill
monthly and after major upgrades. Review model quality, licenses, sources and
portable-runtime versions annually. Recheck filing rules and dates at the time of
use rather than relying on an annual review.
