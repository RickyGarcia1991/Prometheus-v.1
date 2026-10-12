# Planner reliability qualification — 2026-10-12

Decision: reject all three experimental planner changes. The installed signed
dev8 release remains selected and running; no dev9 release was built, signed,
staged or activated. This commit contains this report only. Experimental source
and raw qualification evidence are retained in the host task directory.

## Scope and measurements

The installed model was llama3.2:1b-instruct-q4_K_M, local Ollama, a 4096-token
context and six CPU threads. No model weights, default model preference, cloud
inference, owner permissions or personal memory were changed. Tests used a
disposable synthetic memory database.

The existing dev8 baseline passed its six final application cases, but the two
JSON questions each needed fallback after an invalid plan and an output-limit
error. Their elapsed times were 11.985 and 12.156 seconds. This was already
documented in MODEL-RELIABILITY-2026-10-12.md.

The experimental transport/schema and authorization unit suite passed 56 checks.
That did not establish correct model tool selection. The initial live predicate
checked final answers too narrowly and reported 8/8; inspecting actual plans
revealed unnecessary system/summary calls on all three self-contained JSON
requests. Reassessing those cases against the stronger predicate gives 5/8.

| Experiment | Correct application behavior | Raw model-call errors | Decision |
| --- | ---: | ---: | --- |
| Schema with host example | 5/8 | 0 | Rejected |
| Schema with empty-plan example | 4/9 | 4 | Rejected |
| JSON-delimited request and catalog | 0/4 | 7 | Rejected |

The last experiment was a targeted four-case probe, not a repetition of the
entire nine-case set. These small, different samples are diagnostic results,
not comparable general accuracy benchmarks.

An apparent warm-response improvement in the first candidate (12.156 to 2.297
seconds on the repeated Lima JSON request) is not a deployable performance gain:
that candidate selected an unnecessary tool and was rejected. Its first JSON
request took 14.781 seconds. No broad speed improvement is claimed.

## Stronger acceptance criteria

- Self-contained JSON requests must produce the requested exact JSON with no
  tool evidence, an empty plan, and one planning attempt.
- Hardware questions must select only the registered system/summary tool,
  complete it successfully, and use one planning attempt.
- A source-search paraphrase must select registered project/search, find the
  requested definition with a file and line, and use one planning attempt.
- Exact stored-memory and existing definition-lookup cases retain their
  attributed/evidence-based checks.
- Raw model errors, all plans and failed candidate outputs remain visible.
  Qualification drivers now return a nonzero exit code when any case fails.

Additional prompts included:
- Return only JSON with keys note and enabled. Set note to the literal string
  [7] and enabled to false.
- Which operating system and how much RAM does this computer have?
- Find where conversation_context is declared in the project source and show
  matching lines.

## Findings and next work

A format-constrained plan can still be irrelevant or fabricated. The candidates
selected unnecessary tools, invented arguments, reached output limits, and
sometimes produced no final answer. Existing Core controls remained in force;
the tests supplied no action approvals and production was not changed.

The next reliability work should qualify tool selection against these stronger
criteria before any model or planner is promoted. Unit-test success and correct
final text alone are insufficient. Voice work and physical portability testing
remain deferred at the user's request.

Local evidence receipts: LIVE-RELIABILITY-initial.json, LIVE-RELIABILITY-candidate2.json, PLANNER-PROBE.json and
ASSESSMENT.json. The raw records contain full messages and runtime responses;
the separately stored final candidate source hashes allow later reproduction.
