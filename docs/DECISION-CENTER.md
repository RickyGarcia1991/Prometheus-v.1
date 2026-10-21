# Decisions and health

Dev42 adds an owner-authenticated read-only attention view. It groups the
current browser session's pending proposal, the owner's retained waiting jobs,
recent activity, and cached resource mode. Unconfirmed outcomes sort first;
failed evidence remains visible even when execution completed.

The view uses fixed labels, bounded counts and existing-panel navigation. It
does not expose source, prompts, credential material, file names or identities.
Grouping combines display categories only, never execution claims or approvals.
Counts describe records, not distinct defects or scheduler priority.

Reading does not consume or expire approvals, arm queued work, expire waiting
records, sample resources, or dispatch anything. The normal status endpoint
provides a cache; the attention read accepts that cache for at most 15 seconds.
Telemetry freshness remains unknown without a valid explicit observation
interval. Saved job state never establishes current remote connectivity.

The client polls at most every 30 seconds while visible, unlocked and idle.
It clears rows on busy, hidden, disconnected or locked state and rejects delayed
responses. Snapshot rows expire after 60 seconds even if a request hangs.
Unavailable sources cannot produce an empty all-clear result.

Approvals, cancellation and recover-without-rerun remain in their exact existing
screens. Request-changes editing and a cross-capability durable resolution
inbox remain future work. This is not new reviewer qualification or a change
to the Surface worker, admission limits or model lifecycle.

## Cached connection observations (dev47)

Decisions & health includes a separate device source supplied by the existing
Connected computers provider inventory. It distinguishes current online/offline
counts, unknown availability, an unselected destination, connection failures and
stale observations. These fixed summaries contain no account/device identifiers,
names, commands, provider messages or source content. A saved passing worker
result remains historical evidence, independent of current device availability.

A successful existing connect, refresh or verified pre-dispatch check can record
an inventory observation. The cache is valid for at most 30 seconds using both
wall and monotonic elapsed time. Refresh/reconnect/error invalidates positive
claims; disconnect clears observations. Late callbacks from an older connection
generation cannot republish availability. Ambiguous provider statuses are unknown.
The dashboard itself calls only the pure cache snapshot; it performs no provider
request, selection, pending-review expiration, job recovery or dispatch.

The owner-authorized endpoint validates the bounded cache again before projecting
fixed text. The client clears a current device snapshot at its observation deadline,
even if the next request stalls; fresh rendering of a cached record does not extend
its lifetime. Its existing 30-second polling limit and hidden/busy/authentication
invalidation remain. Refreshing the dashboard does not refresh Connected computers.
Use its normal explicit Refresh action when a new provider observation is needed.

Native action admission and signed worker receipts remain the authority for actual
work. The health display grants no capability and changes no queue destination,
worker identity, permission, result evidence or approval requirement.
