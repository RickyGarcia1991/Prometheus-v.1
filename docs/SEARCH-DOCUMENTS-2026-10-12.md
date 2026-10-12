# Firecrawl search and portable MarkItDown documents — dev11

## Web search

Open **Owner workspace → Public reach · online → Web search · Firecrawl**. Enter a topic and Send. Prometheus sends only that search query to `https://api.firecrawl.dev/v2/search`, retrieves up to five public HTTPS source links and snippets, and stores the result in your existing conversation history. The query can contain up to 400 characters. Search results are evidence, not instructions; pages are not opened automatically. Select **Web page text** to read a supported public URL separately.

Limited keyless access is the default. Firecrawl controls service availability and allowances. Quota, authentication and rate-limit failures produce actionable messages; Prometheus does not retry or silently switch providers. If you deliberately configure `PROMETHEUS_FIRECRAWL_API_KEY` in the host launch environment, it is used for that computer's searches and may consume the associated plan. Keep that key on the host; no key is bundled on the SSD or saved in search results. No SDK, container, browser service or account-cookie import is required.

The client uses Prometheus's DNS-pinned HTTPS transport, a fixed service endpoint, a bounded JSON POST, a 35-second overall network deadline, cancellation, a 1 MB response cap, no redirects and no automatic page scraping. Source links, retrieval time and a response hash remain available in result details. Normal conversation does not automatically browse. Core `reach/read` includes the `web-search` action and retains explicit external-network authorization.

Provider documentation: https://docs.firecrawl.dev/features/search and https://docs.firecrawl.dev/api-reference/endpoint/search.

## Documents

Open the document workspace, expand **Import a document or a new version**, and choose a file. PDF, DOCX, PPTX and XLSX use bundled MarkItDown 0.1.8 converters; TXT, Markdown and CSV retain their existing local text reader. Imported originals and extracted text remain in the SSD's versioned document library. The current 8 MiB input limit applies. Use **Search my documents** for keyword retrieval or **Ask with evidence → My imported documents** for a source-backed answer when a local model is available.

Conversion runs in a short-lived isolated process. It does not upload documents, contact a cloud model, use plugins, execute Office macros, or evaluate spreadsheet formulas. It has bounded runtime, memory, output and archive expansion. Selected pinned upstream converters and their dependencies are distributed with the signed release, including licenses and SHA-256 artifact records. No package installation is needed when the SSD moves to another compatible Windows x64 computer using its bundled Python 3.12 runtime. Temporary extraction stays in a host-local temporary directory and is removed after conversion.

Scanned PDFs still need OCR before import. This update adds text/table extraction, not OCR, password recovery, faithful page-layout reproduction, old binary Office formats, macro-enabled files, audio transcription or cloud document intelligence. A source date/URL is metadata only and is not fetched. Existing document versions, source metadata and conditional-update protections remain intact.

Upstream: https://github.com/microsoft/markitdown. Exact converter and dependency artifacts are recorded in `src/prometheus_assistant/vendor/markitdown/`.

## Release and recovery

The signed dev11 package is installed beside dev10, with SSD recovery and a separate internal-disk recovery copy. Activation verifies the current release, stages the new signed payload and tests the updated bootstrap, requests a cooperative application close, creates a verified backup with an isolated restore check, and atomically selects the new release. Owner identity and personal memory are preserved. The existing dev10 release remains available for rollback.

The portable software and document library travel on the SSD. Internet service access still requires the current computer's network, and optional paid-service credentials remain host-specific. Physical acceptance on a different computer is a separate test; relocatable-path tests do not establish that every host is compatible.
