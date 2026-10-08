# Status — Prometheus 0.8.0.dev1 (October 8, 2026)

Current milestone: integrate sourced research with the default local agent and durable offline recall.
The source line builds on checkpoint caeec01, which already contains the local Core agent, bounded tools,
explicit approvals, offline archive search, self-evaluation, personality settings, and portable runtime support.
The earlier 0.3.0 milestone and October 7 SSD notes are historical.

Delivered in this increment:
- Retrieved research runs through the agent evidence lifecycle in interactive and one-shot modes.
- Source URLs are attached to research responses even when the small model omits them.
- Research contexts retain retrieval timestamps and source-content hashes. Empty evidence stops the turn.
- --retain-research explicitly saves successful research to a separate untrusted SQLite cache.
- Relevant cached research is available to the default agent offline. Explicit cached-research requests
  return bounded stored excerpts deterministically, avoiding unnecessary model planning or hardware tools.
- Pytest imports this checkout instead of an older editable installation. SSD deployment derives its
  source version from pyproject.toml; signed Controller 0.5.4 and Host Agent 0.6.2 remain independently versioned.

Validation and practical limits are recorded in RESEARCH_INTEGRATION_2026-10-08.md.
Online retrieval still requires explicit enablement and a compatible HTTPS JSON search endpoint.
No live external provider has been validated in this increment. Local-model smoke uses labeled synthetic
research evidence and a temporary database. Self-evaluation checks execution and grounding structure;
it is not a factual correctness score. Memory and research remain plaintext, lexically retrieved context,
not model training. Physical eject/reinsertion and a full controller lifecycle were not re-tested here.
