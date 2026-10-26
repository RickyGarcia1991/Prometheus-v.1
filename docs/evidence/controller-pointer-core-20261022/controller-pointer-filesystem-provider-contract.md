# Isolated Windows filesystem provider qualification contract

Scope: generated temporary roots only. This is a test adapter and subprocess harness, not a production controller installer or activation CLI. Frozen transaction core is unchanged pending review. No live profile, scheduled tasks, SSD, installed controller or existing security-state files are used.

## State and trust boundary

The parent test creates a unique temporary directory and a random 32-byte HMAC key. Protect that key with Windows DPAPI CurrentUser and fixed test-domain entropy. Persist only its protected bytes in the temporary root. Fresh subprocesses independently unprotect it. Wrong entropy, corrupt ciphertext or missing key must refuse; never regenerate a key when any transaction history exists. Same-user malicious process resistance is not claimed.

A test-root marker binds a generated nonce, expected canonical root path and synthetic host/volume identities. The harness passes its expected root/identity independently of the journal. Reject reparse roots and children. The adapter must neither discover production roots nor accept journal-provided file paths. Every filename is fixed or a validated transaction identifier.

Provider exclusive() holds a Windows file handle opened without sharing for the entire transaction. A second process must fail or wait boundedly; it cannot advance while the first owns the lock. This proves local adapter serialization only, not production lifecycle/eject mutex compatibility.

## Durable operations

- read/write_intent: fixed transaction filename, maximum size, exclusive creation, Flush(true), exact readback. Existing bytes are never overwritten. Core validates the authenticated envelope against independently supplied plan.
- stage: publish generated immutable payloads to a same-root cache via temporary directory and rename, verify the copied closure. It MUST NOT change the floor or pointer. Do not call Sync-Controller: that routine advances the floor. Test staging alone leaves both unchanged.
- verify: generated fixture closure/hash checks only. This is not a substitute for production trusted certificate, provenance and signature verification; that integration remains separately gated.
- set_floor: read all valid primary/recovery records; refuse malformed history rather than assume zero. Publish and flush a new recovery record before primary replacement. Compare monotonic version under the held lock. Crash after durable recovery but before primary must retain the higher floor.
- replace_pointer: exact expected byte comparison under lock, same-directory flushed temporary file, Windows atomic replace, exact readback. Do not use a journal-supplied path. Preserve original bytes in authenticated intent. On changed pointer refuse without overwrite.
- write_receipt: deterministic bytes, flushed create-only publication; exact existing receipt is an idempotent success, differing receipt refuses. Pointer and floor completion cannot imply UI readiness or process launch.

## Fresh-process fault matrix

Run a first subprocess with one explicit test-only fault label, then terminate/exit it at the boundary. Start a new subprocess with the same independent plan and DPAPI key. Assert no reused in-memory provider state.

Cover before/after intent persistence; before/after cache publication; before/after recovery-floor publication; before/after primary-floor replacement; before/after pointer replacement; before/after receipt publication. Include actual abrupt process termination at representative flushed boundaries, not only caught exceptions. Assert original metadata remains authenticated, unrelated fields remain equal, floor never decreases, and final receipt reports launched=false.

Negative cases: modified intent/tag, corrupt or absent protected key with history, changed pointer (including unrelated metadata only), changed fixture payload, wrong expected host/volume/root, duplicate metadata keys, second concurrent writer, malformed floor recovery history, and floor above both qualified versions. Explicit forward recovery may reach .12 from .11; later ordinary resume must not lower it. Record process exit codes and exact durable-state hashes.

Run under configured Python plus Windows PowerShell/pwsh helper as needed for DPAPI/native filesystem calls. Report runtime coverage precisely; testing Python on Windows does not establish PowerShell 5 compatibility for a future production adapter.

## Completion boundary

Passing this contract establishes isolated durable-state recovery and authenticated journal/key behavior on Windows. Production adapter design, exact physical SSD/profile and trusted signed package binding, lifecycle lock order, actual ownership/pause checks, launch/readiness and reboot/reinsertion checks remain unfinished. No production readiness claim follows from these tests.
