# Historical application activation evidence

Each newly approved application improvement records its exact owner, consumed decision ID/fingerprint, promotion job and candidate fingerprint before invoking the promoter. The link lives in the bounded `ApplicationLinks/outcomes.sqlite3` sidecar; legacy decision, project and capability stores are unchanged. A failed start write prevents dispatch. The fresh consumed-row guard exists only in memory and expires or disappears on restart; evidence never restores it.

The native activation writer carries the candidate fingerprint in every phase. An owner-authenticated activation-status refresh can append selected native facts only to an existing exact link. A mismatched job/candidate is rejected. Missing candidate proof, unsupported states, failed status reads and ambiguous dispatch returns are unconfirmed. A legacy job without the new link remains missing evidence; Prometheus does not infer an approved decision from its time, candidate hash or position as the latest job.

History reads the sidecar without calling the engine, checking live processes, refreshing native results, writing observations or claiming/dispatching work. The lifecycle is always **historical**. A recorded activation does not prove which release is selected now or its current health.

Application events contain only fixed statuses, exact job/candidate identifiers, a local observation timestamp and a digest of selected facts. They exclude native error text, paths, logs and credentials. Each decision has at most 32 observations; there are at most 4096 links and a 32 MiB database. Identical observations are idempotent. A job cannot be adopted by another owner, decision or candidate. Malformed data makes evidence unavailable rather than silently reporting success.

| Observation | Historical lifecycle |
| --- | --- |
| No exact link | Missing; execution is unverified |
| Recorded start or unconfirmed dispatch/result | Unconfirmed |
| Exact queued/validating/retesting/signing/waiting-for-shutdown/starting phase | Pending; completion unconfirmed |
| Active | Recorded activation; no current-health claim |
| Failed | Recorded failure |
| Rolled back | Previous application release restored; this is not project Undo |
| Recovery required | Restoration unconfirmed and needing attention |
| Active together with failure/rollback/recovery-required, or rollback together with recovery-required | Conflicting; no winner is selected |

A native failure followed by rollback is legitimate: both observations remain and the lifecycle reports restoration. A rollback observed alone does not invent an earlier failed observation. Late pending or uncertain refreshes cannot erase a previously recorded terminal result. Native terminal contradictions are retained. None of these states permit a retry, change an approval, sign code, select a release or initiate recovery.

The dashboard distinguishes application activation from worker queues, configuration changes and manual project restoration. Failure and recovery produce warnings; contradictory application results require attention. All references remain read-only and scoped to the same owner, exact application job and candidate, with a separate identity namespace from project and worker evidence.
