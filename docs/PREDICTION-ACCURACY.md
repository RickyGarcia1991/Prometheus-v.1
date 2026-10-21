# Forecast accuracy diagnostics

This implements the adaptive-resource-manager plan's prediction-error measurement step. It does not tune admission policy or claim calibrated resource savings.

At successful reservation, ProjectResources snapshots forecasts before checks execute: direct-process peak RAM, direct-process CPU seconds and separately job-associated CPU seconds. The server binds the snapshot to the exact yielded reservation object and candidate digest. Copied JSON, altered public forecasts, expired reservations and repeated completions cannot supply or replace the snapshot. The UI passes its existing server-owned reservation directly; request/model fields are never used as forecast authority.

Only same-scope complete successful native measurements produce pairs. Unknown forecast or actual remains absent, never zero. Zero actual is valid. Diagnostics report absolute error and underprediction counts, not division by zero or unsupported percentage claims. Job CPU retains partial descendant coverage; direct RAM is not whole-job memory.

A separate SQLite store retains at most 500 runs, numeric forecasts/outcomes, anonymous device/check-family categories and opaque random deduplication IDs. No prompts, paths or code are stored. Dedupe survives reopen within retention; trusted active reservation identity additionally prevents replay after retention or restart. Incomplete reservations are not recovered into accuracy records. Reopening retains completed summaries only.

Diagnostics failures do not change resource admission, native check outcomes or undo. Existing histories and policy remain separate. These figures describe successful observed workloads only; they do not establish accuracy for failures, unobserved workloads, physical disk I/O, battery drain, or end-to-end elapsed duration.
