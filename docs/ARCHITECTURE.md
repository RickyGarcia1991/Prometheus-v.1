# Architecture Notes

The Control Center is intentionally dependency-light. Its first responsibility is to make the rest of Prometheus easier to operate safely.

## v0.1 modules
- `config.py`: deterministic environment-backed paths.
- `health.py`: local runtime and writeability checks without reading secret values.
- `manifest.py`: deterministic SHA-256 file manifests.
- `archive.py`: atomic ZIP snapshots with embedded integrity metadata and secret-file exclusions.
- `cli.py`: stable operator commands (`init`, `doctor`, `snapshot`, `verify`).

## Integration rule
Do not import model SDKs, database clients, or hardware drivers into this package. Those belong behind adapters in the main Prometheus core so the control layer remains usable when other subsystems are broken.
