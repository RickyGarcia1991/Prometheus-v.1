# Live web research — 0.8.0.dev2

Prometheus can query three credential-free public services concurrently:
- Mwmbl: general web search snippets. Linked pages are not automatically downloaded.
- Wikipedia: introductory article excerpts, labeled as tertiary reference material.
- Crossref: publication metadata and abstracts when supplied; full papers are not downloaded.

The federated provider combines at most six distinct URLs. Individual outages are shown in
provider diagnostics; a complete outage or empty result stops the request. Responses are bounded
to 2 MB per service with a 12-second socket timeout, use HTTPS, and reject cross-host/downgrade
redirects. No private conversation, durable memory, model weights, or source files are uploaded.
Only the requested search text is sent. The natural-language search command prefix is removed
and outbound query text is capped at 500 characters.

## Use

In configured chat, type /web retrieval augmented generation. Use /offline to disable research
and /online to enable it again. Normal local questions do not trigger provider calls.

Without loading Ollama:

    START_PROMETHEUS.cmd research "retrieval augmented generation" --json
    START_PROMETHEUS.cmd research-status --json

For an answer synthesized locally:

    START_PROMETHEUS.cmd --online-research --research-provider federated --retain-research ask "Search the web for retrieval augmented generation" --json

Use --offline to override enabled startup settings, --research-provider wikipedia (or mwmbl,
crossref) to select one service, and --no-retain-research to disable evidence-cache retention.
A custom --research-endpoint continues to select the existing HTTPS JSON adapter.

## Configuration and memory

Configuration is read from PROMETHEUS_RESEARCH_CONFIG when explicitly set; otherwise from
PROMETHEUS_RESOURCE_ROOT/research.json. Without either, the code defaults to research disabled.
The file accepts only online_enabled (boolean), provider, and retain_research (boolean).
See config/research.example.json. This machine's SSD configuration is enabled for federated
research and retention, as requested; it is not committed as a global default.

Successful research can be retained in the separate untrusted SQLite cache. Research-only
commands save a research-only session when retention is enabled. Ask/chat preserve ordinary
conversation history and optionally retain bounded source evidence. Model synthesis stays in
local Ollama. Prefetched evidence bypasses model tool planning and enters the Core evaluation
path directly. Explicit cached-research recall returns stored excerpts with their provenance.

## Limits

Live retrieval does not establish factual correctness or guarantee the newest information.
Web snippets, encyclopedia excerpts, and publication metadata have different evidentiary value;
the prompt and source labels preserve those distinctions. Public index coverage and availability
vary. This setup is not a comprehensive news service or a substitute for reading full primary
sources. The deterministic self-evaluation checks execution structure, not truth. Cached excerpts
are plaintext, may become stale, and do not train model weights.

## Provider references

- Mwmbl project: https://book.mwmbl.org/
- MediaWiki search: https://www.mediawiki.org/wiki/API:Search
- MediaWiki extracts: https://www.mediawiki.org/wiki/Extension:TextExtracts
- Crossref REST API: https://www.crossref.org/documentation/retrieve-metadata/rest-api/

Fresh validation results are recorded below after testing.
