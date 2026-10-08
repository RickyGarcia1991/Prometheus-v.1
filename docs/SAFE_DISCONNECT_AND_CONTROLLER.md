# Safe disconnect and portable controller (Windows)

## Fail-closed safe-disconnect workflow

Run the SSD-root script `Prometheus-Resources/Tools/portable-safe-disconnect.ps1`
with `-Root <SSD-root>` after closing Prometheus, the controller, Ollama and
other programs accessing the SSD. The default mode only checks and reports.
It never kills processes and never requests device removal.

The preflight checks the expected volume label, cooperative memory lock,
running drive-backed processes and SQLite quick_check. Any failure blocks
removal. A passing preflight does **not** mean it is safe to unplug:
Windows must separately confirm safe removal.

`-RequestWindowsRemoval` is an explicit opt-in that first repeats the
preflight and calls the existing USB-identity-verified Windows removal helper.
It must not be used over a remote-control connection hosted on the same SSD:
the connection may be interrupted. It has not been exercised on hardware.

The previous controller eject orchestrator still exists and has not been
replaced. It force-terminates processes and must not be considered equivalent
to the new graceful preflight. Do not use that old workflow for live memory.

## Portable controller

Run `Prometheus-Resources/Tools/portable-controller-setup.ps1 -Root <SSD-root>`
with `-Mode Check` to inspect requirements without changes. The optional
`-Mode InstallHostAgent` delegates to the existing signed, UAC-authorized
host-agent installer. `-Mode LaunchController` delegates to the existing
controller launcher, which respects intentional-close state. Neither operation
bypasses security authorization.

Controller portability is Windows-only and requires .NET 10 Desktop runtime.
Host-agent installation is per-host, with explicit Windows approval.

## Verification limits

Automated tests verify fail-closed wiring and portable memory guard behavior.
The busy-SSD check was exercised on the original laptop and correctly refused
removal. Physical ejection and another computer have not been tested.
