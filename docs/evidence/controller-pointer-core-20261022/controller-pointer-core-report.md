# Controller pointer transaction core — development qualification

Implemented a provider-based transaction core only. No production adapter, command-line entry, live profile mutation, tasks, installation or drive writes were performed.

18 tests pass (controller-pointer-core-tests.log): faults before/after durable intent, staging, floor mutation, pointer replacement and receipt publication; idempotent resume; journal/key/pointer/package/volume mismatch refusal; explicit forward recovery; preserved unrelated metadata; duplicate-field refusal.

The HMAC-authenticated immutable intent binds exact original metadata bytes and exact generated candidate/recovery metadata bytes, transaction, host, volume, and both signed manifest identities. The original metadata remains available verbatim; new JSON preserves unrelated field values. Provider signature verification is required for source and staged cache. Forward recovery is explicit and never lowers the floor. A completed recovery pointer remains recovery on later invocation. No launch occurs or is claimed.

## Remaining integration gates

- A qualified native adapter must implement actual physical SSD/profile identity and reparse refusal, lifecycle/eject lock ordering, inactive-controller and pause checks.
- Bind provider verification to the reviewed HostAgent signature/provenance/version checks and immutable staged bytes. Stage recovery before any floor change.
- Protect the HMAC key with current-user DPAPI and independent trusted custody; this core accepts a key only as an internal dependency and has no public recovery bypass.
- Implement exclusive flushed create-only intent, durable monotonic floor, same-directory atomic metadata compare-and-swap and receipt publication. Test real filesystem faults and fresh-process restart on supported Windows runtimes.
- Integrate signed package self-identity and deployment authorization. Launch/readiness remains a separate acceptance boundary, with intentional close preserved.

Core SHA-256: d97d946fe52b0f1cde3e9362e9be3d42d199cc4f503b9ef983dfd9d62f001445
Tests SHA-256: b7fab5ce4cdd1e1da3eeb84536322dea03088c2d20a3edbfa5ddbc36a798618f
