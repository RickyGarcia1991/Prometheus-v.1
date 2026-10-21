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

## Inspect whether a saved result still applies

Open the original saved project and choose **Check whether inputs still match**
beside a completed check. This explicit inspection compares the current
interpretation, collected evidence and reviewed source text with the inputs of
that saved check. It reads only the original bounded file selection and does not
run a worker, claim a queued job or create a new approval.

The historical check result and current applicability are separate. A failed
check remains failed even when all inputs match. Changed inputs are stale;
missing inputs or changes detected during inspection are unavailable. The
observation time is shown because this is a point-in-time comparison, not a live
guarantee or a transaction preventing future edits. Source correspondence uses
the reviewed text; it does not claim original byte-for-byte BOM preservation.

The inspector verifies the exact saved result identity and does not rewrite its
evidence, resolve the conflict or establish runtime correctness. A new check
still requires the ordinary exact review and approval. Signing out, switching
projects or hiding the page invalidates a pending display response.
