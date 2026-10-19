# Bounded checks following evidence interpretations

An owner can request a fresh Python syntax or fixed-profile Ruff check from an
active, completed interpretation of conflicting evidence. Open a project, open
**Evidence follow-up checks**, select the interpretation, list one to eight saved
Python files, choose the check and destination preference, and explain why the
new check is useful. Review the exact source, interpretation, observations,
destination, worker and tool before approving or rejecting it.

The result supplements the original evidence. A clean syntax or lint result does
not establish runtime correctness, select a winning interpretation, hide a failed
check, repair files or qualify a model. These controls cannot run arbitrary commands
or submitted Python code. Existing syntax/lint workers and their resource limits
remain in use.

## Waiting and recovery

Approval permits a single claim and at most one hour of waiting at the reviewed
destination. Reading the follow-up panel while unlocked arms idle resumption.
Ordinary queued checks and follow-up checks alternate when both are ready. A busy
Surface job stays assigned there; it does not move to the Dell without another review.

Before the claim, Prometheus checks that the interpretation state, collected native
evidence, saved source files, policy and tool identity still match. A changed or
unavailable input cancels waiting work and requires a fresh review. The claim is
serialized with interpretation revisions and withdrawals across processes. Native
observations and project files are separate stores: their check binds the snapshots
read at that boundary, not a global atomic snapshot of the computer.

An undecided review is held only in the authenticated session and disappears after
restart. A decision interrupted before durable queueing stays visible as unconfirmed
and grants no automatic restart authority. An uncertain claimed job can query its
original receipt, even if its interpretation later changed; recovery never dispatches
the job again. A later withdrawal cannot undo a claim that already occurred.

## History and compatibility

The owner decision remains in canonical decision history. A separate append-only
association preserves the exact interpretation, source family, evidence snapshot,
request and result relationship. History shows follow-ups beside their own decision,
their original interpretation, its effective revision and the original evidence
decision. Failed checks and tool failures remain distinguishable from clean results.
Malformed or mismatched records are shown as unavailable rather than empty success.

The separate follow-up store and its queues live under the decision-history data
root, and must be included in backup and restore. Dev53 can preserve these files
and read the compatible canonical decisions, but does not display or resume this
new queue. Rollback cannot bypass the new evidence checks.

This first bounded implementation retains at most 100 associations, including
rejected and interrupted attempts. It stops accepting new associations when full;
it does not silently delete evidence. Each history projection displays at most 16
linked attempts, with an explicit remaining count. Maintenance and broader check
types remain future work. This feature does not complete the broader roadmap.
