# Owner decision history and requested changes

An exact project-change review offers Approve, Deny, and Request changes. Request changes requires an owner note of at most 2000 characters and ends the original approval. It does not apply files or automatically ask a model to revise them. A matching live response can load a revised draft into the currently selected project. The draft uses exact review, clears the public research query, and requires an explicit Prepare action. A new proposal needs a new approval; the previous review cannot authorize it.

Drafts from saved history, another observed tab, another project, an expired decision, or a repeated response cannot replace the current draft. Opening history never loads a revision draft. Feedback is preserved while refreshing the same pending review and cleared when that review resolves or a different proposal appears.

Owner decision history displays up to 50 saved approve, deny, or request-changes receipts, their timestamps and owner feedback. It is loaded only when opened or explicitly refreshed while online, visible and idle. Authentication loss, hidden/busy state and disconnection clear displayed history and invalidate in-flight reads. Text is rendered literally; no source data is stored in browser storage. History provides no execution buttons and does not authorize or resume any work after restart.

History failure is shown as unavailable rather than an empty successful result. Durable receipt creation, owner filtering and restart behavior are enforced by the server; client summaries are not authority. Existing scheduled-work and activation receipt contracts remain separate.


## Linked execution evidence

Owner history can show up to 16 ordered immutable outcome events per decision for supported exact and delegated project-edit execution. Approval without linked events is not confirmation that work ran or succeeded. A started execution without a terminal result is unconfirmed; history does not automatically resume it. Older decisions without outcome links remain readable.

Native check summaries show planned, executed, passed, failed, cancelled and timed-out counts. They describe recorded checks, not model acceptance or general behavioral correctness. Missing checks, zero executed checks, and applied-without-checks outcomes remain unverified. Failed checks remain visible even when automatic rollback restored the original content; execution outcome and restoration are separate facts.

Manual undo links only to previously recorded edit identifiers. Interrupted undo is restoration unconfirmed; failed restoration remains visible alongside the original execution outcome. These historical events survive restart without becoming approval, replay, recovery or execution controls. Denied and request-changes decisions authorize no execution. Other capability outcome links and older unlinked edits are outside this bounded scope.

Malformed, contradictory or unavailable evidence is reported as unavailable, never as an empty successful history. No raw project paths, source, commands or model content are rendered in the outcome timeline. All history text is read-only and literal.
