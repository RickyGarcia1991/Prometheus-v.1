# Portable component updates

Maintenance now has **Show update status**, **Check next component now**,
**Enable automatic checks**, and **Pause automatic checks**. The reviewed catalog
ships with automatic checks enabled, as requested by the owner. A later pause is
remembered on the data drive. Checks run only while Prometheus is open and idle;
one due component is processed per maintenance pass. The Cancel control can stop
an active component check. Existing source watches and component checks alternate
when both are due.

## What is supported

The current catalog covers 162 components and resource groups: 14 installed
Ollama model tags, six Python packages, Python/Ollama runtimes, 76 imported
documents, 15 additional manufacturer links, 35 offline archives, five source
indexes, the signed application and eight support-file groups. Support groups
are coverage placeholders, not a claim of a complete per-file software inventory.

Forty-eight entries can check publisher metadata through PyPI, GitHub or the
Ollama registry. Package/runtime discovery does not install executable code.
Model tags are compared by immutable manifest digest; a changed manifest does
not inherit automatic qualification and is not downloaded or activated by this
manager. Existing model routing and recorded failures are preserved.

Four MIT-licensed open-polsia reference documents can import a verified newer
revision. The manager checks the exact license bytes, resolves an immutable
commit, retains downloaded and transformed SHA-256 hashes, preserves attribution
and license text, and requires the existing document to match the reviewed
baseline. Source text and embedded prompts remain untrusted reference material.
It does not install the upstream agent service or connect commercial accounts.

An import appends a version using the supported Documents library, then checks
the retained bytes and actual keyword search/citation. A failure appends the
prior version again. Atomic version checks protect edits made by another caller.
An interrupted update retains a recovery journal; conflicts stop for review
without overwriting intervening user changes. Old documents and conversations
are not erased.

## Limits and activation

Pinned archives, proprietary/link-only material, locally authored templates and
unconfigured feeds are explicitly shown as unavailable for automatic checks or
application. Their next revision needs a reviewed source/import recipe. An
upstream code repository changing does not rewrite its original catalog summary.

Executable packages, runtimes and the application still require the existing
verified release process: build a new edition, run relevant regressions, sign and
verify the manifest, close the idle app, back up and verify restore, activate with
rollback, then complete live acceptance. Model replacement additionally needs
fresh task-quality evidence tied to the candidate's exact weights/configuration.
This manager intentionally has no operation that marks a discovered model as
qualified or silently overwrites active signed code.

## Storage and networking

State, leases, immutable snapshots, staged documents and recovery records live
under `Interface/Data/Maintenance/Updates` alongside portable user data, and are
included in the supported Maintenance backup. No drive letter is fixed in the
catalog. Retained staging is bounded to 64 MiB/4096 entries; reaching a limit
reports a failure while preserving previous revisions. Checks are daily, with
six-hour retry backoff after failures. A manual check handles one next component.

Only public component identifiers go to their registered publishers. No chats,
owner profile, credentials or private project files are uploaded. HTTPS requests
validate public DNS, pin the connection address, refuse redirects and compressed
responses, enforce byte/time limits and reject incomplete payloads. DNS and
network work can be cancelled without creating unbounded resolver threads.

Publisher API references: [PyPI JSON](https://docs.pypi.org/api/json/),
[GitHub commits](https://docs.github.com/en/rest/commits/commits),
[GitHub releases](https://docs.github.com/en/rest/releases/releases), and
[Registry V2](https://distribution.github.io/distribution/spec/api/).
