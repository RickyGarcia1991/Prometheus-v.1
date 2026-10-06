# Decision Log

## D-0001 — Local-first control layer
**Status:** Accepted for foundation.

Prometheus should have a small dependency-free control layer that remains functional even if model, database, network, or UI components fail.

## D-0002 — Integrity-verified archives
**Status:** Accepted for foundation.

Project snapshots must contain a per-file SHA-256 manifest and a separate SHA-256 checksum for the completed archive.

## D-0003 — Non-destructive first integration
**Status:** Accepted for foundation.

Until the existing Prometheus repository and machine are reachable, new tooling will be created separately and must not overwrite unknown existing code.
