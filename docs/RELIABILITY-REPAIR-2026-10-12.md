# Reliability repair — 2026-10-12

Signed dev9 repairs both ordinary interface chat and the agent path. Explicit
supplied JSON data is formatted directly; complete explicit numeric sorting uses
exact Decimal comparison while preserving numeric tokens. Shared stored-memory,
project-definition and OS/RAM/thread requests use attributed data or registered
Core tools. Matching a request does not approve a tool: existing Core and host
capability denials remain in force, and extra-action requests do not take shortcuts.

Generated JSON is validated before persistence, including finite numbers, unique
object keys, portable Unicode and bounded explicit top-level key contracts. The
same contract is passed as an Ollama JSON schema for generation. Planner-written
summaries remain in raw diagnostics and cannot masquerade as observed tool output.
Fallback remains bounded, cancellation does not save partial answers, and source
metadata is retained without appending prose to JSON.

## Verification

- 327 distinct automated cases passed across focused and related suites,
  including real synthetic WebAuthn signatures, HTTP owner gating, scoped grants,
  authority denials, memory pressure, Unicode, and JSON formatting. No skipped cases.
- 22 live cases passed across Interface.execute and run_agent: 18 deterministic or
  registered evidence cases and four model-generated translations. No raw model
  call errors or unintended tool calls occurred in that final run.
- The first resumed live run failed two of 20 cases: wrong JSON field in the UI
  and an unsorted array from the agent. Those failures are retained. Numeric sort
  now belongs to exact application computation and is NOT counted as successful
  model reasoning. An additional translation case checks genuine generation.
- The final live receipt precedes a narrow dotted-key rejection fix and whitespace
  normalization; the changed rejection case and related suite passed afterward.
- The initial local suite encountered sandbox socket restrictions. After granting
  localhost network access, all seven affected HTTP checks passed. The final
  focused suite then ran together successfully.

Tests use separate synthetic memory and host authority directories. Existing owner
credentials, personal memory, model preferences, model weights and network/tool
permissions are not changed by this source patch. Voice and physical portability
remain separate work. These samples are not a general accuracy or reliability
guarantee; unrecognized requests still depend on the existing model/planner.

Ollama format schema reference: https://docs.ollama.com/capabilities/structured-outputs
