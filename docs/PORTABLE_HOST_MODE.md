# Portable Host Mode (Windows x64, initial implementation)

Prometheus's primary conversation memory is now on the Prometheus-2TB SSD at
`Prometheus-Data/memory.sqlite3`. The original host memory database is retained,
not overwritten. The migration script refuses to replace an existing SSD database.

## Run on a compatible Windows host

1. Connect the SSD and wait for Windows to mount it.
2. Open the SSD root in File Explorer. The drive letter need not be D:.
3. Run `START-PROMETHEUS-PORTABLE.cmd` from the SSD root, optionally with
   `doctor --json`, `sessions --json`, or `chat`.
4. The launcher checks for the portable Python runtime, Ollama runtime, model
   library, core release, and existing portable memory before running.
5. Stop Prometheus and any Ollama process using the drive, then use Windows
   Safely Remove Hardware before disconnecting it.

No new empty memory is silently created when the portable database is missing.
The launcher sets `OLLAMA_NO_CLOUD=true` and uses loopback model serving.
It may write temporary logs and controller state to the current Windows host.

## Scope and limitations

This is Windows x64 portability, not Linux/macOS compatibility.
Another physical Windows computer has **not yet been tested**.
The SSD launcher references the current core release
`Prometheus-v0.8.0-dev1-8c17eab`; future release updates must update it.
Windows PowerShell and sufficient permissions to run local executables are required.
Some controller, watcher, and host-agent functions remain host-specific and
must be installed/configured per host, with user approval.
The scheduled backup tasks currently exist on the original laptop; they do
not automatically follow the SSD to another computer.
Portable SSD memory is plaintext. Protect the drive physically and consider
encryption and a separate offline backup.

## Validation

The portable SSD memory was verified using SQLite integrity and a temporary
backup/restore drill. The current SSD launcher passed `doctor --json` and
`sessions --json`; a real model question was answered and saved to SSD memory.
Unit tests verify non-overwriting migration and missing-source failure.
These checks cannot replace a test on a second physical host.
