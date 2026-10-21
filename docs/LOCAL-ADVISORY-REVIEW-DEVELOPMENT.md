# Local advisory review development

This source checkpoint follows installed dev27 revision 2. It is not an installed
release or a completed multi-provider reviewer system.

Project preparation now sends the exact proposed bytes to one eligible local
reviewer before creating the exact approval. The editor's existing newline/BOM
conversion is used, and candidate/files are revalidated after review and before
apply. The app's single task executor serializes editing and review generation.

Author and reviewer identities come from bounded local model inventory observed
before and after generation. All generation steps for a grouped edit must agree
before one author artifact is recorded. Different model aliases do not establish
different artifacts. Inventory identity is not immutable execution attestation or
proof of independent reasoning.

The adapter uses an already running loopback Ollama server. It does not start a
runtime, download models, invoke tools, send files remotely, or silently truncate
source. Resource, context, timeout, cancellation and response-qualification
outcomes remain explicit. Reviewer output never becomes authorization or a test
result. High/critical advice on a delegated task returns to exact owner review.

Approval and completed-result panels show findings and provenance separately from
deterministic evidence. Approval applies the retained report; it never generates a
new opinion. The verification council keeps failed/missing checks blocking and
preserves high-risk concerns. Actual rollback and undo evidence survive reporting
failures.

Saved conversations retain a bounded advisory-only record and selected result
labels. Candidate source, credentials and executable approval records are not
stored by this new metadata path. Historical review records are displayed without
restoring approval authority or rerunning inference. Existing history that never
saved advisory evidence cannot be backfilled as a real review.

Acceptance includes exact bytes, owner cancellation, stale files, no review replay,
failed-check rollback, delegated high-risk escalation, trusted artifact continuity,
untrusted text rendering, Unicode retention and malformed-history recovery. Live
model qualification and browser acceptance are recorded separately in the task's
outputs. The multi-reviewer roadmap still includes broader provider support and
review-quality evaluation.
