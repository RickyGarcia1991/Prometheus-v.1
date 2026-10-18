# Owner decision history and requested changes

An exact project-change review offers Approve, Deny, and Request changes. Request changes requires an owner note of at most 2000 characters and ends the original approval. It does not apply files or automatically ask a model to revise them. A matching live response can load a revised draft into the currently selected project. The draft uses exact review, clears the public research query, and requires an explicit Prepare action. A new proposal needs a new approval; the previous review cannot authorize it.

Drafts from saved history, another observed tab, another project, an expired decision, or a repeated response cannot replace the current draft. Opening history never loads a revision draft. Feedback is preserved while refreshing the same pending review and cleared when that review resolves or a different proposal appears.

Owner decision history displays up to 50 saved approve, deny, or request-changes receipts, their timestamps and owner feedback. It is loaded only when opened or explicitly refreshed while online, visible and idle. Authentication loss, hidden/busy state and disconnection clear displayed history and invalidate in-flight reads. Text is rendered literally; no source data is stored in browser storage. History provides no execution buttons and does not authorize or resume any work after restart.

History failure is shown as unavailable rather than an empty successful result. Durable receipt creation, owner filtering and restart behavior are enforced by the server; client summaries are not authority. Existing scheduled-work and activation receipt contracts remain separate.
