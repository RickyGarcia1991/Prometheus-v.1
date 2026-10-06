# Local document search

```cmd
START_PROMETHEUS.cmd search "C:\path\to\documents" "delivery target"
START_PROMETHEUS.cmd search "C:\path\to\documents" "delivery target" --json
```

Choose the directory explicitly. Search reads UTF-8 .md, .txt and .csv recursively. It ranks
lines by distinct matching query words and returns five literal excerpts with relative source
filename and one-based line number. Matching is case-insensitive. Ollama is not needed;
nothing is saved or uploaded.

No match means no matching evidence; it does not prove a fact false. Unreadable, non-UTF-8 and
oversized files are reported as skipped. Hidden directories and links are excluded. Limits:
500 supported files, 1 MB per file, 20 MB total and 4,000 query characters.

Retrieved text is content, never executed or fed to a model. This milestone exposes evidence
directly, avoiding generated citations or instruction following from retrieved text.

Acceptance: exact filename/line/excerpt; absent evidence reports no match; works without
Ollama; skips unsupported/unreadable formats; bounds collections; chat regression tests pass.

Next increment: PDF/Word extraction, richer ranking and optional grounded synthesis require
format handling, citation validation and prompt-injection tests before implementation.
