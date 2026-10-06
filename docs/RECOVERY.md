# Recovery and rollback

## Private conversation history

Default database: %LOCALAPPDATA%\Prometheus\memory.sqlite3. History and backups are plaintext.
Source releases and model recovery materials exclude chats. SQLite's backup API supports an
active source. Use a new filename each time:

```cmd
START_PROMETHEUS.cmd backup-memory "%LOCALAPPDATA%\Prometheus\backups\memory-NEW.sqlite3"
START_PROMETHEUS.cmd restore-memory "%LOCALAPPDATA%\Prometheus\backups\memory-NEW.sqlite3" "%LOCALAPPDATA%\Prometheus\recovered-NEW.sqlite3"
START_PROMETHEUS.cmd --memory "%LOCALAPPDATA%\Prometheus\recovered-NEW.sqlite3" sessions
```

Keep the .sqlite3.json sidecar with the database. Restore verifies checksum/integrity/counts
and refuses an existing destination. Inspect recovered history before explicitly selecting
it with --memory. Copy private backups to a separate private drive to survive disk loss.

## Source and Python

Keep release ZIP and .sha256 together. Verify outer SHA-256 and embedded manifest before
extracting to a new directory. Packaged Python 3.12 plus standard library requires no pip
installation for normal use. Run START_PROMETHEUS.cmd --help and doctor --json.

## Ollama and selected model

The separate offline-recovery directory contains official Ollama 0.35.1 Windows AMD64 archive
and only llama3.2:1b manifest/blobs, with hashes in RECOVERY_MANIFEST.json. Verify before use.
Extract the runtime to a new directory. With no server on the chosen port, set OLLAMA_MODELS
to recovered models, OLLAMA_HOST to 127.0.0.1:11435 and OLLAMA_NO_CLOUD=1, then run ollama.exe serve.
Run START_PROMETHEUS.cmd --base-url http://127.0.0.1:11435 doctor --json and a synthetic ask.
Never include private Ollama keys or unrelated models. Preserve model license blobs.

## Stable deployment and rollback

Desktop launcher targets %LOCALAPPDATA%\Prometheus\releases\0.2.0. The previous launcher text
is saved in local app-data. Restore it to roll back; original development source and live
history are preserved. A reboot/native double-click visual check is separate from CLI checks.
