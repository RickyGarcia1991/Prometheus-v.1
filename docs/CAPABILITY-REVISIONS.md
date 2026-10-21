# Capability revisions and shared evidence

Dev48 extends Request changes to runtime settings, idle-research settings,
integration actions and access, direct syntax checks and scheduled verification.
The shared panel displays the exact pending review and accepts up to 2,000
characters of owner feedback. Request changes closes that proposal before any
setting write, adapter call, worker probe, source transfer, signature or queue claim.

The owner then changes the original controls and explicitly prepares a new review.
That proposal gets a fresh identity and fingerprint. Worker proposals reread the
current source and get a new job identity. Existing integration access cannot turn
a replacement into an immediate execution. Each replacement needs its own decision.

A one-use, ten-minute context connects the reviews. It binds the authenticated
owner, browser binding, conversation, capability kind and server-derived subject.
Runtime and research settings refer to the local capability; integration subjects
bind adapter/operation or adapter access; worker subjects bind the validated
project root. Changed files, settings or destinations remain part of the fresh
exact review. Context is never restored from history, persisted in browser storage,
shown in result details or returned to another browser binding. Definite rejected
submissions retain the browser context; uncertain transport outcomes discard it.

Capability feedback receipts live under CapabilityChangeRequests, and lineage
under CapabilityRevisionLinks. Existing decision, project lineage and outcome
databases retain their older schemas and semantics. Dev47 can still read its
original records after rollback; it does not display the new capability feedback.
Restart discards pending reviews and live contexts while preserving audit links.

History also labels existing same-owner decisions that already reference the
identical native worker job, request and observed result. Bounded related-decision
references are read-only provenance. They do not adopt evidence, create approvals,
share another owner's records, change a verdict, resume a queue or repeat a job.
Similar file hashes and revision ancestry alone cannot create this relationship.
Contradictory or malformed native evidence remains an explicit history failure.

This milestone does not change model qualification, Surface worker identities,
source execution policy or the limits enforced by existing execution controllers.
