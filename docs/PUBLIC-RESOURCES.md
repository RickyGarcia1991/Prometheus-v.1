# Public research, legal workflows and retained editions

This release adds eight live publisher searches, a broad international discovery directory, an offline public reference index, four local models, versioned research snapshots, and signed core repair. It does not contain every public document or train the models on everything downloaded.

From the SSD root, these commands work without loading a language model:

```bat
START-PROMETHEUS-PORTABLE.cmd research providers
START-PROMETHEUS-PORTABLE.cmd sources --subject law
START-PROMETHEUS-PORTABLE.cmd sources --subject jobs
START-PROMETHEUS-PORTABLE.cmd library public "service summons"
START-PROMETHEUS-PORTABLE.cmd legal plan
START-PROMETHEUS-PORTABLE.cmd --online-research research search "civil rights" --provider ecfr
START-PROMETHEUS-PORTABLE.cmd --online-research research refresh "civil rights" --provider ecfr
START-PROMETHEUS-PORTABLE.cmd research history "civil rights" --provider ecfr
START-PROMETHEUS-PORTABLE.cmd --online-research legal refresh-rules
```

`refresh` saves a new content-addressed JSON snapshot beside the selected memory database in `Public-Research`. It never replaces an earlier edition. `history` verifies hashes and labels the newest saved version separately from historical versions. Newest saved does not mean current online. `refresh-rules` preserves each distinct official civil rule PDF; a changed PDF requires review before calculator use. Existing offline archives and the previous software release are retained. Refreshes are explicit commands, not a scheduled background download.

Search providers: `federal-register`, `ecfr`, `europe-pmc`, `clinicaltrials`, `crossref`, `nasa`, `loc`, `canada-open-data`. Searches send the supplied query to that publisher. No keys are needed for these connectors; other catalog services may require accounts, fees or different permissions. No private case files are uploaded by these commands. API excerpts and metadata are discovery evidence, not complete source documents.

For a model-assisted research answer, choose a publisher explicitly:

```bat
START-PROMETHEUS-PORTABLE.cmd --online-research --research-provider europe-pmc ask "Search current evidence on diabetes prevention"
```

The agent also has `knowledge/public`, `knowledge/versions`, `legal/intake`, `legal/calendar` and an approval-gated `research/public` tool. Retrieved content cannot grant permission to run code, edit files, or enable more network access.

## Legal procedure and deadlines

`legal plan --input facts.json` accepts country, region, court, issue, event_date and case_stage. It identifies missing facts and produces an ordered research/drafting checklist with sources, including courts, legislation, judges, legal aid, lawyer directories and employment agencies. It does not choose jurisdiction from guesses or submit anything.

Copy `config/legal-deadline-example.json` to your case folder and fill the actual facts. The supported calculator is **FRCP 6 for U.S. federal civil proceedings, day-based periods of 1–366 days**. It requires the governing period's source and rule, court, trigger date and exact service provision. It does not determine statutes of limitation, tolling, or which rule applies. Other countries, state rules, appellate deadlines, hours/months/years and automatic extensions are not implemented.

```bat
START-PROMETHEUS-PORTABLE.cmd --online-research legal deadline --input "C:\YourCase\deadline.json"
```

It checks the current official civil-rule PDF against the installed edition before an online calculation and stops if the document changed. Offline results explicitly lack this currency check. All results remain provisional because local orders, legal holidays, service facts, filing cutoff and court accessibility require case-specific verification. `verified_checks` records user attestations, not independent proof. A changed or rejected filing requires comparing the official notice and rules, revising the draft, and recording the reason; it does not justify silently extending an expired deadline. Consult a qualified local lawyer or court self-help service for unresolved case-specific questions.

The five original rule PDFs remain in `Prometheus-Resources\Knowledge\Public-Reference\2026-10-09\documents`. Civil, evidence, criminal and bankruptcy texts are indexed with PDF page references. The appellate PDF's extracted character map was unreadable, so its original PDF is retained without claiming searchable full text. The index also contains a discovery directory and small research result samples, not a complete medical or global legal corpus.

## Models and defense

New local models: `qwen3:0.6b`, `qwen3:1.7b`, `qwen3-embedding:0.6b`, `gemma3:270m`. The Qwen chat models are conservative fallback candidates; existing model priority and RAM reserves remain. Explicit `--model qwen3:0.6b` selects that installed model. The embedding model is available to Ollama for embedding clients; it is not a chat model and the current SQLite index uses full-text search. Gemma 270M is a tiny experiment, not an automatic high-stakes answer model. No model-weight fine-tuning was performed. See the installation report for file verification versus inference tests.

Network retrieval uses HTTPS, certificate verification, explicit publisher hosts, public-IP checks, DNS pinning, response limits and no redirects or environment proxies. Source text is evidence, never executable instructions. Signed core verification checks file hashes and rejects unexpected files in the Python source directory. With the portable session lock held, known damaged files can be restored from the matching signed recovery copy; changed files are preserved under `repair-history`. Personal memory, rule logic and authorization state are not automatically rewritten.

This detects many corrupt or altered files, but it cannot make a compromised Windows account, administrator, mutable launch bootstrap or runtime trustworthy. Keep Windows security protections enabled and retain an independent backup. Recovery refuses invalid signatures, a mismatched release, path traversal, junctions, unknown injected source files and a damaged backup.
