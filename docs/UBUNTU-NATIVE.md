# Ubuntu native support checkpoint

This branch carries the Ubuntu deployment changes on top of dev55. It adds
native microphone capture, Silero speech detection, local Whisper transcription,
eSpeak output, and a pinned native remote connector. The UI opens in Chat, with
Workspace and Review navigation and the optional owner backup PIN in Settings.
Small hosts prefer the compact vision model while retaining the memory reserve.

## Runtime layout

Use a layout such as:

- ROOT/app/VERSION: this repository checkout
- ROOT/runtime/owner-auth: private Ubuntu python3-fido2 package
- ROOT/runtime/ubuntu-voice: extracted packages plus models
- ROOT/runtime/remote-linux/client: locked mcp-remote client
- ROOT/runtime/remote-linux/CLIENT-MANIFEST.json: the pinned client inventory
- ROOT/data: private owner data, kept outside the source checkout

The native assets are included in the separately verified Ubuntu flash checkpoint;
they are not committed as application source. Do not substitute Windows DLLs or
the portable Windows Python dependency tree for native Ubuntu crypto.

The deployed system is Ubuntu 26.04.1 amd64, Python 3.14, Node 22.22.1.
Its system dependencies include alsa-utils 1.2.15.2-1ubuntu1,
libespeak-ng1 and espeak-ng-data 1.52.0+dfsg-5build1, and
python3-cryptography 46.0.5-1ubuntu2.2. Private owner authentication uses
python3-fido2 2.0.0-1. These are observed versions, not a guarantee that other
Ubuntu versions or architectures work.

The voice package source paths, versions and SHA-256 hashes are in
[voice-packages.json](../tools/ubuntu/voice-packages.json). They were obtained
from Ubuntu's package archive and extracted into the private voice directory.
The corresponding system libraries still need to be present. Models obtained
from ggerganov/whisper.cpp on Hugging Face:

| File | SHA-256 |
| --- | --- |
| ggml-base.en.bin | a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002 |
| ggml-tiny.en.bin | 921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f |

The Silero model remains in the repository's existing vendor/voice_activity
bundle. Native Linux code uses its model with the Ubuntu whisper library.

The remote dependency lock and client inventory are in tools/ubuntu. Rebuilding
requires npm ci --ignore-scripts --omit=dev with the supplied package files,
excluding node_modules/.bin symlinks from the copied client. Verify every file
against REMOTE-CLIENT-MANIFEST.json; do not update the application's pinned
manifest hash merely to make a differing installation pass. The complete
verified client is also in the flash checkpoint. /usr/bin/node must be
OS-owned and not group/world writable. OAuth enrollment is per owner/machine;
credentials must never be copied into the repository or public diagnostics.

## Launch and administrator review

From the checked-out source directory:

    python3 -I tools/ubuntu/run_prometheus.py --runtime-root /ABSOLUTE/ROOT/runtime --memory /ABSOLUTE/ROOT/data/memory.sqlite3 ui --port 8765 --no-browser

The launcher selects native Ubuntu cryptography before the portable source
dependencies. The running deployment uses a user systemd service with
Restart=on-failure, RestartSec=5, UMask=0077 and NoNewPrivileges=true.
PROMETHEUS_KEEP_OPEN=1 disables the normal five-minute idle exit for this service.

Administrator proposals only enter a user-owned review queue. The separate
desktop terminal displays the exact command and requires the owner's matching
approval phrase followed by Ubuntu sudo authentication. No password is stored,
no passwordless rule is installed, and the application service cannot elevate.
The queue is an audit convenience, not a tamper-proof boundary against code
running as the same OS user. A declined review is not successful root execution.

The four-digit PIN is a local application fallback, not a hardware security key
or disk-encryption credential. Five incorrect attempts disable PIN sign-in until
a genuine passkey assertion resets it. Enrollment and changes require a passkey.
Existing owner credentials, PIN state, OAuth tokens, chat history, and recovery
keys are excluded from the source and runtime checkpoint.

## Verification boundaries

Mocked Windows contract tests can execute on Ubuntu; they do not demonstrate
native Windows behavior. Real Windows-only checks retain their platform
requirements. The upstream Browser Use worker requires Windows Python 3.12.
Ubuntu supplies the equivalent bounded public-page reader through OS-managed
Node and Chromium. This is a separate native adapter, not the upstream Python
Browser Use package, and does not qualify interactive browser agents.

Check voice against known phrases, including word error rate and input clipping,
and delete test recordings. Synthetic direct decoding and speaker/microphone
loopback are separate from testing the owner's normal speech.

A working Desktop Commander connection used to deploy these changes does not
prove Prometheus's own OAuth connection works. That connection needs its own
completed sign-in, selected device, reviewed action, and verified result.

The flash checkpoint supplements the accepted Windows recovery archives and
keys. It is not a bootable image, a full HDD clone, or a backup of private owner
data. Repository publication and passing functional tests do not establish a
successful CodeQL scan or absence of vulnerabilities.


## Native resource adapters (26 October 2026)

Connect the existing mounted HDD library with PROMETHEUS_RESOURCE_ROOT and set
PROMETHEUS_ZIM_RUNTIME to ROOT/runtime/ubuntu-zim. The ZIM adapter is qualified
for Ubuntu 26.04 amd64 / Python 3.14; it verifies the three native object hashes
before loading. Package provenance is in tools/ubuntu/zim-packages.json.
Windows executables and model files on the shared HDD remain Windows resources;
Ubuntu advertises its actual running Ollama inventory.

Native document import supports bounded PDF, DOCX, XLSX and PPTX text extraction.
PDF uses the pinned pypdf wheel from the existing vendor bundle; Office formats
use validated XML. It does not perform OCR, preserve page layout, execute macros
or calculate spreadsheet formulas. Password-required PDFs are rejected.

The local qwen3-embedding:0.6b model enables document semantic retrieval; its
observed digest is ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d.
Four synthetic synonym-retrieval cases passed. This is not a broad accuracy
benchmark. Models stored for Windows are not automatically usable on Ubuntu.

The native public-page reader uses fixed OS-owned /usr/bin/node and
/opt/google/chrome/chrome. Chrome retains its sandbox. A fresh profile, rejecting
proxy, request interception and parent-side public HTTPS validation limit the
reader to one host and GET requests; private destinations, redirects, downloads,
credentials and forms are excluded. These application controls are not a proof
of isolation against browser vulnerabilities. Cancellation terminates the owned
process group. Only this reader has been exercised, not interactive agents.

The voice UI says 'Start speaking now' only after the capture state reports an
open microphone. It distinguishes preparation, recording and transcription.
Browser state tests use a synthetic microphone status, not a real recording.
For an owner speech check, choose Text mode, press Record, wait for the cue,
speak normally, then review the words before sending.

Native health reads NATIVE-CHECKPOINT.json and NATIVE-SOURCE-MANIFEST.json,
separately from accepted Windows release metadata. Checksums cover listed files;
this record is not a release signature, a CodeQL scan, or model acceptance.
See RESOURCE-VALIDATION-2026-10-26.md for evidence and remaining plans.
