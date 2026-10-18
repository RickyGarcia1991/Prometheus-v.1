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
