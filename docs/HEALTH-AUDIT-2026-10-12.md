# Prometheus health audit repairs — dev12

This update fixes defects reproduced during the full installation audit:

- Replies now receive the available reading space; the artwork becomes compact during conversations. Long replies start at their beginning and remain keyboard-scrollable. Sending a question or loading history reveals chat and closes the owner overlay without discarding workbench drafts. The manual Read answer aloud button is removed; Text only, optional Spoken + text, and dictation remain.
- Scheduled source/update maintenance no longer inserts unsolicited status dumps into the conversation. Explicit checks still display their results, and failures remain available in maintenance status.
- The Control Center doctor now creates its write-permission probe exclusively with a unique name. It preserves existing files and removes only its own temporary probe.
- A checksum scan that reaches its time limit now retains any missing, changed, or unreadable files it has already found. Known failures stay failed; an incomplete scan without known failures stays blocked. Neither case claims that the remaining files were checked.
- The browser document-import handler now accepts PPTX and XLSX, matching the installed MarkItDown converters and file picker. The 8 MiB limit and rejection of unsupported file types remain in place.
- Continuous integration includes Windows x64 Python 3.12 and browser-script tests, retains Linux core coverage, and installs the pinned authentication test dependency. Native-only tests are identified explicitly instead of failing on unsupported operating systems.

Use **Owner workspace → Public reach · online → Web search · Firecrawl** for topic search. In Documents, use **Import a document or a new version** for PDF, DOCX, PPTX, XLSX, TXT, Markdown, or CSV, then **Search my documents** or **Ask with evidence → My imported documents**. Documents are converted locally with dependencies stored on the SSD.

The passive **Run health check now** command checks the selected release, listed file hashes, database structure, installed model files, runtime presence, and current RAM estimate. Its physical-device and model-task rows explicitly describe checks it does not execute. Actual model results are separate under **Test available helpers**. File presence does not establish answer accuracy, and a working speech file does not establish that a person heard audio from a speaker.

Microphone/speaker acceptance needs an intentional spoken test and a listener. A second-computer portability test needs the SSD moved to that computer. Installed account-based tools need their supported sign-in; a CUDA-only media package needs compatible GPU hardware or a deliberately selected CPU workflow. These conditions must remain visible rather than being labeled green without evidence.

The release is installed beside its signed dev11 predecessor. Signing, backup, isolated restore, owner protection, portable paths, and rollback checks remain part of activation. Audit evidence records the exact scope and results separately; this document does not promise that all possible operations or future external services are error-free.
