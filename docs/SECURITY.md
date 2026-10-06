# Security Baseline

- Keep secrets in environment variables or a dedicated secret manager; never in Git.
- Default to least privilege for plugins, remote shells, databases, cloud accounts, and hardware.
- Require explicit authorization for destructive or externally consequential actions.
- Keep an audit trail for tool invocations and configuration changes.
- Treat downloaded models, datasets, plugins, scripts, and archives as untrusted until verified.
- Pin critical dependencies and record hashes where practical.
- Backups are not valid until restore/verification procedures are tested.
- Remote Desktop access should be restricted to the minimum directories required for Prometheus.
