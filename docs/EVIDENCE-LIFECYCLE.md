# Historical evidence lifecycle and conflict isolation

The owner history projection summarizes recorded facts for project executions,
worker jobs and immediate capability observations. It does not inspect current
files, contact a provider, replay a job, adopt a digest, write history or restore
approval authority. All lifecycle summaries have `freshness: historical` and
`read_only: true`. Timestamps are display metadata, never identity, expiry or a
way to choose between conflicting results.

`evidence_lifecycle.for_owner(owner, *, decisions, project_events,
capability_events)` accepts complete bounded canonical decision receipts and
validated native event collections before the fifty-row display limit. The
projection also checks exact owner, approval ID, fingerprint, kind and event
sequence. It returns a map keyed by `(approval_id, fingerprint)`.

Every entry has exactly `schema_version: 1`, `state`, `source`, `freshness`,
`reference`, `reason`, `linked_decisions`, `related_decisions`, `truncated` and
`read_only`. Sources are `none`, `project`, `worker` or `capability` (an immediate
native observation). The public `validate_projection(value)` rejects malformed
or semantically contradictory summary metadata.

| State | Reason | Meaning |
| --- | --- | --- |
| not_applicable | decision_not_approved | This decision authorized no execution. |
| missing | no_recorded_start | Approval alone does not confirm any execution. |
| pending | execution_pending | A start, wait or defer observation exists; present activity is not inferred. |
| unconfirmed | outcome_unconfirmed | Unknown/unconfirmed outcome remains unresolved. |
| unconfirmed | restoration_pending | Explicit Undo was started but has no saved terminal result. |
| unconfirmed | restoration_unconfirmed | Explicit Undo failed or automatic rollback needs attention. |
| recorded | native_result_recorded | A terminal observation is recorded; this is not a success judgment. |
| restored | project_restored | The final explicit Undo observation records restoration. |
| conflicting | native_identity_conflict | Distinct decisions record inconsistent facts for the exact native identity. |

Recorded failures, tool failures, cancellations, expiration and saved settings
remain their original statuses. A successful automatic rollback does not rename
the failed project result to a manual Undo. Each decision's explicit restoration
state comes only from that decision's own sequence; related decisions do not
inherit it. A revision chain does not invalidate or copy execution evidence.

Worker identity is owner + job ID + exact request digest. Project identity is
owner + project hash + nonempty native edit ID, in a separate namespace. Empty
edit IDs and unknown project results cannot form shared groups. Equal evidence
hashes, equal project roots, timestamps, or revision ancestry cannot establish a
shared execution. The identity hash is an owner-local reference, not authority.

Different original project result facts or worker terminal facts within an exact
identity mark every affected family member conflicting. No winner is selected;
the original events/counts remain visible and unchanged. Other identities remain
readable. Malformed schemas, missing starts, invalid sequences or invalid receipt
bindings still fail closed as unavailable data; they are not ordinary conflicts.
Project Undo attempts lack their own shared operation ID, so separate restoration
attempts are not compared as if they were one execution.

`shared_capability_evidence.for_owner(owner, events, *, project_events=None)`
retains the existing consistent worker references and adds consistent
`project_result` references. A contradictory identity is omitted from this shared
result list and exposed by lifecycle metadata instead. Pending worker decisions
may reference the same native identity but cannot borrow a peer's terminal result.

A non-null native reference has a count including the current decision and up to
16 other exact decision references; omitted references are reported by the total
and truncation flag. A null reference always has count zero, no related references
and no truncation. Data belonging to another owner is excluded before grouping.

No database schema or persisted observation is changed. The retained release can
read the same legacy receipts and sidecars. Historical evidence is not described
as current validation or as expired merely because time has passed; checking
current source validity would require a separately recorded native binding.
