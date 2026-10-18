# Owner decision history and requested changes

An exact project-change review offers Approve, Deny, and Request changes. Request changes requires an owner note of at most 2000 characters and ends the original approval. It does not apply files or automatically ask a model to revise them. A matching live response can load a revised draft into the currently selected project. The draft uses exact review, clears the public research query, and requires an explicit Prepare action. A new proposal needs a new approval; the previous review cannot authorize it.

Drafts from saved history, another observed tab, another project, an expired decision, or a repeated response cannot replace the current draft. Opening history never loads a revision draft. Feedback is preserved while refreshing the same pending review and cleared when that review resolves or a different proposal appears.

Owner decision history displays up to 50 saved approve, deny, or request-changes receipts, their timestamps and owner feedback. It is loaded only when opened or explicitly refreshed while online, visible and idle. Authentication loss, hidden/busy state and disconnection clear displayed history and invalidate in-flight reads. Text is rendered literally; no source data is stored in browser storage. History provides no execution buttons and does not authorize or resume any work after restart.

History failure is shown as unavailable rather than an empty successful result. Durable receipt creation, owner filtering and restart behavior are enforced by the server; client summaries are not authority. Existing scheduled-work and activation receipt contracts remain separate.


## Linked execution evidence

Owner history can show up to 16 ordered immutable outcome events per decision for supported exact and delegated project-edit execution. Approval without linked events is not confirmation that work ran or succeeded. A started execution without a terminal result is unconfirmed; history does not automatically resume it. Older decisions without outcome links remain readable.

Native check summaries show planned, executed, passed, failed, cancelled and timed-out counts. They describe recorded checks, not model acceptance or general behavioral correctness. Missing checks, zero executed checks, and applied-without-checks outcomes remain unverified. Failed checks remain visible even when automatic rollback restored the original content; execution outcome and restoration are separate facts.

Manual undo links only to previously recorded edit identifiers. Interrupted undo is restoration unconfirmed; failed restoration remains visible alongside the original execution outcome. These historical events survive restart without becoming approval, replay, recovery or execution controls. Denied and request-changes decisions authorize no execution. Older unlinked edits remain unlinked.

Malformed, contradictory or unavailable evidence is reported as unavailable, never as an empty successful history. No raw project paths, source, commands or model content are rendered in the outcome timeline. All history text is read-only and literal.

## Other reviewed capabilities

Dev45 adds exact decision links for runtime startup preferences, idle research configuration, integration access, individual integration calls, direct remote syntax checks, and scheduled syntax/lint jobs. Each uses a separate bounded CapabilityLinks database. Existing decisions and project-outcome databases are not migrated or rewritten.

A settings save records the committed configuration, not execution of research or a change to an already running model server. Issuing integration access is distinct from performing an integration action. An adapter response means the adapter returned; it does not independently verify the response's meaning or truth. Unknown effects remain unknown after an exception.

Worker observations bind the exact job and normalized request digest as well as the approved owner, decision and fingerprint. Waiting, cancellation, expiration, uncertainty, tool failure and completed native checks remain distinct. Exact trusted receipts can complete a previously linked uncertain result after restart. Reading decision history cannot recover, replay, sign, dispatch or create a job. Older jobs with no capability start remain unlinked.

The separate scheduled-job status operation already expires waiting permissions. It also reconciles the existing local queue state into already-linked capability evidence. This can append a cancellation, expiration or retained receipt locally, but does not call a worker or submit work. The decision-history endpoint itself remains strictly read-only.

New execution requires the start record to be durable. If recording the later observation fails, the native result remains intact and the response reports the history gap; repeating the action is not a repair. Semantic duplicate observations do not fill history. Terminal observations are immutable, with at most 16 events per decision and a bounded database without silent eviction. Outcome storage contains only fixed statuses, bounded counts, exact opaque identities and a digest of selected native facts; no source, credentials or provider output.

External application promotion and its activation/health/rollback receipts are a remaining lifecycle boundary. A promotion request is not treated as proof that a release was activated successfully.
