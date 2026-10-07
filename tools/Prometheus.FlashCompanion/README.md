# Prometheus Flash Companion

Canonical source for the Corsair recovery/bootstrap flash-drive companion.

Safety contract:
- Preserve recovery/checkpoint data.
- Stop and checkpoint Prometheus before removal.
- StoppedVerified means Prometheus is saved/stopped, but Windows release verification is still required.
- ReadyToEject is set only after the Windows process-reference verification passes.
- The native panel then requests normal Windows safe removal; it does not force eject.
- Any detected external process reference fails closed.

The deployed USB copy lives under Prometheus-Companion.
