# Controller transaction independent acceptance checklist

Scope: development-only provider transaction core. Generated providers must record every requested mutation and launch. Passing this checklist does not qualify a live adapter, physical reinsertion, reboot, or protected UI.

## Intent authority and replay

- Missing, malformed, unsigned/unauthenticated or modified intent must refuse recovery before any mutation. Authenticating a journal must bind its exact transaction, host/volume context, original pointer and candidate/recovery identities.
- Transplant a valid intent to another transaction, volume, host, candidate or recovery package: refuse. Completed intent replay must be a no-op or explicit already-complete outcome, not reapply an older pointer or recreate authority.
- Change journal phase without authenticated state: refuse. A forged completed-files list must never authorize deletion or overwrite of pre-existing material.
- Authentic old intent against a newer committed pointer or higher floor must not restore the old candidate. Preserve the newer state and report conflict.

## Metadata preservation and compare-before-write

- Use metadata containing nested unrelated fields and unknown future fields; preserve their values and types through commit and recovery. Keep original bytes as recovery evidence.
- Alter only a non-controller field after prepare and before publish: refuse stale comparison without overwriting it. Repeat during recovery and after pointer publication; receipt completion must not rewrite unrelated edits.
- Unknown current pointer, malformed metadata, missing metadata, or unreadable state must not be treated as permission to restore old bytes. Distinguish genuine absence from access errors.
- Publication must compare the exact expected prior state, write atomically, then read back. A provider's success return alone is not sufficient proof.

## Interrupted publication and durable floor

- Inject failure before/after each durable boundary: intent save, cache publication, recovery preparation, floor publication, pointer replacement, readback, and final receipt save. Resume in a new core instance.
- In particular: pointer replacement succeeds but receipt publication throws. Retry recognizes the exact valid new pointer/cache/floor and finishes the receipt without duplicate publication, floor regression, cache destruction or launch.
- Before floor advancement, old state may remain. After advancement, never claim rollback to a lower release as success. Candidate selection requires its version at or above the freshly read floor.
- Recovery has independently verified signed identity/version and must satisfy the actual durable floor, including a partially published floor recovered from backup. Reject recovery below that floor even if its name or intent claims otherwise.
- Higher floor introduced by another completed transaction must block stale candidate/recovery. Candidate and recovery must not be interchangeable merely because both are signed; each is pinned to its designated role and full package identity.
- Cache absent/corrupt after a committed pointer must remain an explicit incomplete state until verified repair, never mark completion from pointer equality alone.

## No implicit side effects

- All successful, refused, interrupted and replayed paths must have zero launch requests. Launch is a separately qualified operation.
- No implicit process termination, task activation, security-floor reset, broad cleanup, or authorization/user-data mutation. Cleanup only transaction-owned staged resources, if any, with exact ownership validation.
- Serialization covers compare/publish/readback and recovery. A concurrent newer state must win through explicit refusal or a safely retried fresh transaction, never a last-writer-wins overwrite.
- Fault tests assert exact mutation traces and preserved old resources, not only an exception or status string. Recovery must be bounded and idempotent.

## Evidence required at freeze

Record exact core/test hashes, actual commands and results, provider contract assumptions and fault coverage. Clearly distinguish simulated provider atomicity/authentication from real filesystem durability and cryptographic authentication. Preserve initial failures; do not describe a mock journal as authenticated unless its production binding is implemented and independently checked.
