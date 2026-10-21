# Evidence-bound reviewer development contract

Status: connected in development through the post-check runtime; installed dev32 is unchanged. The pure validator does not qualify a reviewer or execute a model.

`reviewer_check_evidence.assess(response, evidence, files)` separates caller-supplied measured failures from reviewer-selected references. It checks source-byte hashes and structured check consistency, rejects the whole report when any finding is invalid, preserves all failures, and exposes omissions. It never grants authorization or production acceptance. Model-supplied line numbers are bounds-checked and explicitly unverified as defect locations.

## Required integration boundary

The caller must supply measurements from a trusted, bounded check runner under the existing owner-approved task scope. Source hashes bind identity, not authenticity or correctness. Browser fields, model prose, arbitrary test stdout and self-asserted signatures must not become trusted structured evidence. The current generic project test runner provides exit status/output; it does not supply the typed input/expected/actual rows used by this protocol. Do not invent those rows by parsing model output.

Preserve the complete proposed snapshot independently of the working tree: failed checks may restore original bytes before review. An omitted or failed check remains a separate failed task even if a reviewer identifies nothing. Review cannot rerun tests, change an approval, suppress failure or disable rollback.

## Original integration requirements (status below)

1. Add a bounded structured measurement producer for explicitly supported checks, with exact snapshot and run identity, using generated fixtures before user projects.
2. Bind the review request to those measurements and complete source; retain local model inventory, context, cancellation and memory checks.
3. Test empty/invalid reports, changed source, changed evidence, timeouts and replay without new execution.
4. Run fresh model acceptance separately from deterministic validation. Historical v6 model detection is 3/4; four successful gate replays are not new inference or model acceptance.
5. Connect accepted output to development UI/history, package and test, then seek separate installation approval.

The pure module remains side-effect-free. The explicit approved-check hook and separate post-check runtime are described below.


## Integrated development checkpoint (2026-10-17)

The approved editor now supplies an explicit check hook to ApprovedCheckCapture. The capture preserves exact proposed bytes, native process exit/cancel/timeout outcomes, and run/candidate identity. It invokes only the planned existing workspace check once. The post-check measured_review_runtime receives this packet after normal rollback and records a separate measured_review; pre-approval advice and authoritative verification remain separate. No source or measurement fields are accepted from browser requests.

The approval describes the optional local post-check review. The task result and saved history display it, including deterministic failed checks and model omissions. Saved detail is strictly validated and bounded to 48 KiB; oversized valid detail retains explicit counts and a nonpassing summary. Model locations remain unverified. Installed dev32 is unchanged.

Native process success is not semantic correctness or test coverage. This integration cannot supply semantic input/expected/actual values and never parses stdout to invent them. Missing observations block review. Existing empty-suite and rollback handling remains authoritative. A fresh acceptance trial uses four generated projects and real unittest process outcomes; reviewer reference detection is graded separately from deterministic failure visibility. The prior model result remains historical until fresh inference completes.

## Post-check evidence reviewer admission

Optional measured evidence review now checks fresh mode/resource admission before model inventory and again before inference. Restrictive preferences defer only the optional model call; deterministic native check outcomes and identity remain available. A failed check cannot become passing because the reviewer is deferred. New resource_deferred results use attempted=false and retain bounded version-2 resource admission evidence, or null if admission itself was unavailable. Legacy saved measured-review records without this field remain readable and do not prove that mode admission ran. Balanced/Performance do not relax existing profile, context, policy, cancellation or memory checks. No retry, OS tuning, running-call cancellation or new model qualification is introduced.
