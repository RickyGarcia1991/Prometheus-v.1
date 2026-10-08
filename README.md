# Prometheus v0.8.0-dev1 — local agent core, portable knowledge, and federated research

Prometheus now provides a small local text assistant using Ollama, plus the original health-check and verified-backup foundation. No new cloud account or paid service is required.

## Start on Windows

Keep Ollama running and double-click START_PROMETHEUS.cmd. On the configured laptop, double-click Start_Prometheus_Offline.cmd on the Desktop instead.

Type a question at You>. Type /exit to close. Each successful exchange is saved locally. A new launch starts a new conversation; resuming a previous conversation is explicit.

From a terminal in this folder:

```cmd
START_PROMETHEUS.cmd doctor
START_PROMETHEUS.cmd sessions
START_PROMETHEUS.cmd chat --session YOUR_SESSION_ID
START_PROMETHEUS.cmd history YOUR_SESSION_ID
```

## Requirements and local storage

Python 3.11+ (Windows py launcher), Ollama, and the downloaded local model llama3.2:1b. Runtime uses Python's standard library only. The small model was chosen for the laptop's limited RAM; it can give incorrect answers.

Default history is %LOCALAPPDATA%\Prometheus\memory.sqlite3 on Windows. It is plaintext, not encrypted, and not included in the source recovery archive. Back it up separately if you need to retain chats. Source backups do not include Python installations or model weights.

The model adapter permits only a local HTTP loopback endpoint, disables proxies/redirects, rejects remote model metadata and cloud selections, and has no cloud fallback. Recent context is bounded; full saved history is retained. Saved conversation text is not automatically verified knowledge or model training.

## Foundation commands

```cmd
py -3 prometheusctl.py doctor
py -3 prometheusctl.py snapshot . --label prometheus-control-center
py -3 prometheusctl.py verify PATH_TO_ARCHIVE.zip
```

For a clean source release, export tracked files with Git before taking a snapshot. Raw snapshot commands exclude .env files (except .env.example), Git metadata, and common virtualenv/cache folders, but are not a general private-data scrubber.

## Development

```cmd
py -3 -m pip install -e .[dev]
py -3 -m pytest -q
```

Optional coverage tooling: coverage 7.16.0; run coverage run -m pytest -q, coverage combine, then coverage report. The supplied .coveragerc measures assistant subprocesses.

Opt-in online research routing with source provenance is implemented for configured HTTPS research providers. Structured schema-v2 knowledge storage is explicit and provenance-bearing; conversation text is not automatically promoted to trusted knowledge. Voice, autonomous tools, semantic search, and model retraining remain future work. See docs/STATUS.md and docs/ai for scope and verification evidence.

## Vocabulary guidance

An editable local vocabulary.json supplies relevant definitions and preferred words to each question. See [docs/VOCABULARY.md](docs/VOCABULARY.md) for examples and limits. Restart chat after editing. The doctor command also checks SQLite integrity and glossary validity.

## Release 0.4.0 development checkpoint

The v0.4 line adds a portable Knowledge Vault inventory and ranked federated source catalog. `resources` reports offline/catalog resources under `PROMETHEUS_RESOURCE_ROOT`; `sources` reports remote research sources and authority tiers. Network use remains explicit and subject to host policy. Community sources are never treated as primary authority.

## Previous release 0.3.0

The packaged Windows release includes Python and uses a stable app-data installation.
Use backup-memory and restore-memory for verified history recovery, and search DIRECTORY QUERY
for local .md/.txt/.csv evidence with exact source citations. These commands do not need Ollama.
See docs/RECOVERY.md, docs/LOCAL_DOCUMENTS.md and docs/EVALUATION.md.

## Research and local recall (0.8.0.dev1)

The default agent keeps retrieved research inside its tool-evidence and self-evaluation lifecycle instead of switching to plain chat. Research excerpts remain untrusted data; answers are instructed to cite source URLs. Retrieval timestamps and source-content SHA-256 hashes accompany each excerpt. Empty research results stop the turn rather than producing an unsupported answer.

Online research requires both explicit opt-in and a configured HTTPS JSON provider. To retain successful research evidence for later offline agent recall, also select --retain-research:

    START_PROMETHEUS.cmd --online-research --research-endpoint https://YOUR-PROVIDER/search --retain-research chat

Replace the endpoint with a compatible provider; no public search service is installed by this change. Without the retention flag, research evidence is not added to the separate cache, although successful chat exchanges remain in normal history. Cached evidence is plaintext SQLite data, carries a timestamp and context hash, and is retrieved lexically from at most 200 recent notes. Agent context uses bounded excerpts. It is never automatically promoted to trusted facts or used to train model weights. Compatibility --no-agent mode does not recall the research cache.

Pytest now selects this checkout's src directory even when an older editable installation is present. Application development, signed Controller 0.5.4, and Host Agent 0.6.2 retain separate versions.
