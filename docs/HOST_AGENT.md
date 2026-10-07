# Prometheus Host Agent

The Host Agent is the Windows-side bridge for the portable Prometheus SSD. It is installed on `C:` and never relies on executing a long-lived process from the removable volume.

## Runtime model

- `Prometheus Volume Watcher` starts at logon and subscribes to `Win32_VolumeChangeEvent` arrival/removal notifications.
- On a genuine Prometheus SSD arrival, the watcher invokes the Host Agent from `%LOCALAPPDATA%\Prometheus`.
- The Host Agent verifies the pinned signing certificate, RSA-SHA256 manifest signature, and every SHA-256 payload hash before using a controller package.
- Controller updates are staged and verified before activation; the prior cache is retained as `.previous` for rollback.
- If the controller is already running, cache replacement is deferred rather than overwriting live binaries.

## Eject contract

`%LOCALAPPDATA%\Prometheus\eject-mode.json` is the shared suppression marker. While it is active, the watcher and Host Agent must not start or repair Prometheus. A real removal followed by a new arrival clears the marker. A marker from a previous Windows boot is stale and may be cleared. Pressing Start in the controller also cancels eject mode.

Remote-management processes, development shells, logs, controller caches, and temporary files belong on `C:`. `D:` is treated as the portable deployment source. Near eject, remote tooling must stop reading `D:` and Windows Kernel-PnP blockers must be checked rather than killing unrelated processes.

## Installer

`INSTALL-PROMETHEUS-HOST-AGENT.ps1` supports `Install`, `Repair`, `Verify`, and `Uninstall`. Install/Repair remove the superseded Device Support and Host Agent event tasks and leave one logon-triggered Volume Watcher task. Elevation uses standard Windows UAC.

## Validation

Before release: parser-check the PowerShell files, run `python -m pytest -q`, run `git diff --check`, verify the installed Host Agent, confirm one controller and no bootstrap CMD processes, and perform physical reconnect plus reboot/logon tests. A fresh-PC test remains the final portability validation.
