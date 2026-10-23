# Linux full-application rehearsal preparation — 2026-10-22

Read-only Surface inventory via Desktop Commander completed on the online Surface. No service, model, worker, credential, environment, package, operating system or display was changed. This preparation does not advance platform changes ahead of the current companion milestone.

## Fresh observations

Surface: Ubuntu 26.04.1 LTS, kernel 7.0.0-38-generic, x86_64, glibc 2.43. `/usr/bin/python3` is Python 3.14.4. System environment has cryptography 46.0.5 and psutil 7.1.0; fido2, pytest, numpy, onnxruntime and sounddevice were absent in module-discovery checks.

Existing isolated environment `/home/ricky-garcia/prometheus-env/bin/python` is Python 3.14.4 with pytest 9.1.1, numpy 2.5.4, onnxruntime 1.31.0, tokenizers 0.23.3, faster-whisper 1.2.1, ctranslate2 4.8.2 and av 16.1.0. It lacks cryptography, fido2, psutil and sounddevice. Package presence/version is not successful end-to-end execution.

`/home/ricky-garcia/Prometheus-Ubuntu` is old commit `d426880e54078306920cca0f083c80cfaaa8061e`, with pyproject version dev1. Its optional Linux voice/reranker pins describe that older branch. Do not treat it as the installed Windows dev55 implementation. `/home/ricky-garcia/prometheus-dev55-check-1603d9ea9867897f/check` contains a bounded source/test transfer and pytest wheels only; it does NOT contain `owner_access.py` or `ui_server.py`. Those earlier Surface test passes cannot establish full application/authentication compatibility.

Available executables: `/snap/bin/ollama`, `/usr/bin/node`. `ffmpeg`, `espeak-ng`, `whisper-cli` and `pwsh` were absent from PATH. No inference, runtime startup, model inventory/download or microphone action was performed.

## Exact source requirements and known blockers

Use the accepted dev55 archive SHA256 `df7f38e648c8e5de525cf1e3997e895f8a09bbd4b31ff9cdbbd99a55458c6073` and manifest SHA256 `f093dcd0d45ee0efeb2d9135596f6a255e1c488f48818e69c6a49a0e6ba0fffa`, read directly from Prometheus-dev55-closeout.json. Do not use whichever working checkout happens to have the latest filename. Canonical pyproject currently declares dev31 and no required dependencies despite later application features; packaging metadata alone is insufficient as a Linux dependency lock. The publication checkout provides the inspected current modules, but an actual rehearsal must bind every source byte to the selected accepted release plus an explicitly reviewed Linux overlay.

- [owner_access.py](C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/dev46-release-sync/src/prometheus_assistant/owner_access.py): requires `fido2.server.Fido2Server` and WebAuthn entities for a required owner gate. Missing dependencies keep a required gate locked. A fresh directory without `OwnerAccess/required.json` or the credentials database does not require authentication, so launching a blank application is NOT owner-authenticated acceptance.
- [ui_server.py](C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/dev46-release-sync/src/prometheus_assistant/ui_server.py): binds loopback; owner RP ID is `localhost`, owner origin is `http://localhost:<port>`, and owner data lives beside the selected memory database in `OwnerAccess`. Use that exact hostname/origin for browser enrollment and testing.
- `remote_job_crypto.py` and `remote_worker_service.py` require cryptography for signatures. `remote_windows_crypto.py` explicitly refuses non-Windows. Linux signing/credential behavior requires its own contract; do not copy Windows private state or alter the existing Surface worker identity.
- [voice.py](C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/dev46-release-sync/src/prometheus_assistant/voice.py): transcription selects `Voice/Whisper-b5454/Release/whisper-cli.exe`; capture explicitly requires Windows WinMM; output explicitly requires Windows SAPI/PowerShell. Installing faster-whisper does not adapt these paths.
- [speech_activity.py](C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/dev46-release-sync/src/prometheus_assistant/speech_activity.py) and `vendor/voice_activity/LOCK.json`: Windows x64-only DLL loader for whisper.cpp b5454/Silero 6.2.0. Need reviewed Linux shared-library/backend implementation with exact artifacts, or explicit unsupported status. No full voice pass is available today.
- Windows task/controller, DPAPI, physical-volume/lifecycle locks and native Job Object containment are not supplied by Python source portability. Linux startup/shutdown/removal/ownership and resource containment need equivalent proven behavior or clear unsupported states.

The exact Linux dependency lock remains to be constructed and reviewed. Neither current environment contains the full authentication prerequisites. Do not install unpinned `latest` packages or contaminate the existing worker/test environment to make an import pass.

## Concrete later rehearsal commands (NOT executed)

First prepare a new isolated root containing the frozen source, its manifest and a reviewed `requirements-linux-rehearsal.lock` with exact versions/hashes and matching Linux wheels. A wheelhouse must include fido2, cryptography and transitive requirements, plus pytest for tests. Reuse existing artifacts only after exact provenance/hash verification; no model download is required for initial authentication/interface checks.

```bash
# After companion completion and verification of exact rehearsal contents:
REHEARSAL=/home/ricky-garcia/prometheus-linux-rehearsal-dev55
# Create a new root; refuse to reuse an existing path.
test ! -e "$REHEARSAL" && mkdir -m 700 "$REHEARSAL"
python3 -m venv "$REHEARSAL/venv"
# Stage the verified source/wheelhouse/lock into this new root before continuing.
"$REHEARSAL/venv/bin/python" -m pip install --no-index --require-hashes \
  --find-links "$REHEARSAL/wheelhouse" -r "$REHEARSAL/requirements-linux-rehearsal.lock"
PYTHONPATH="$REHEARSAL/source/src" "$REHEARSAL/venv/bin/python" -B -c \
  'from fido2.server import Fido2Server; from fido2.webauthn import PublicKeyCredentialRpEntity; import cryptography; import prometheus_assistant.ui_server'
mkdir -m 700 "$REHEARSAL/data" "$REHEARSAL/data/OwnerAccess"
printf '{}\n' > "$REHEARSAL/data/OwnerAccess/required.json"
# Confirm chosen port is free before launch; do not stop another listener.
PYTHONPATH="$REHEARSAL/source/src" "$REHEARSAL/venv/bin/python" -B \
  "$REHEARSAL/source/prometheus.py" --memory "$REHEARSAL/data/memory.sqlite3" \
  ui --port 54655 --no-browser
```

These are a command template, not a ready-to-run installer: source verification, wheel hashes, actual supported fido2 version/API, blank-instance enrollment flow and port availability must be resolved first. Keep all test data synthetic and separate from current data. The owner completes a fresh permitted authenticator enrollment at `http://localhost:54655`; existing Windows enrollment is not assumed transferable. Do not copy live credential databases or weaken the required gate.

Acceptance before claiming full application rehearsal: locked startup; missing-dependency refusal; fresh owner enrollment/sign-in/sign-out; wrong-origin/token/owner rejection; selected-project review/deny/apply/check/grouped undo; saved history/restart; resource unsupported labels; no worker enrollment/dispatch caused by dashboard reads; clean shutdown/restart with isolated data integrity. Fixtures prove workflow only. Actual existing Linux model inference, performance, voice, credential restore and host lifecycle are separate follow-on gates.

Read-only probe sessions: 1109107 (OS/system imports), 1109172 (existing roots/test-env imports), 1109237 (old Linux checkout/executables), 1109328 (dev55 subset and package versions). All completed normally. This report does not claim a full application import test: no full current app source exists in the inspected Surface qualification directory, and no app constructor or owner DB was opened.

## Deployment dependency closure correction

Direct inspection of the signed dev55 deployment reveals bundled `src/fido2-2.2.1.dist-info`, `cryptography-50.0.2.dist-info`, `cffi-2.1.1.dist-info`, `pycparser-3.11.dist-info` and `pypdf-6.20.1.dist-info`. Thus dependencies=[] in pyproject is not an absence claim. Fido2 requires cryptography >=2.6, !=35, <52. Cryptography requires cffi>=2.0.0 on CPython. Actual bundled wheel tags are cryptography cp311-abi3-win_amd64 and cffi cp312-cp312-win_amd64, with `_rust.pyd` and `_cffi_backend.cp312-win_amd64.pyd`. These cannot run on Linux.

A full source directory on PYTHONPATH would shadow Linux-venv cryptography/cffi with the bundled Windows packages. Therefore the command template's `source/src` must be a separately staged, recorded Linux overlay that includes the accepted Prometheus packages and data but intentionally resolves these platform dependencies to verified Linux equivalents. Preserve the original signed extracted tree unchanged; record every omitted/replaced file and hashes in an overlay manifest. The unmodified Windows archive is not a Linux-qualified signed release. Linux wheel availability and exact compatibility for fido2 2.2.1, cryptography 50.0.2, cffi 2.1.1 and pycparser 3.11 remain unverified here; use these as the baseline requested pins, not an assertion that Linux artifacts have been acquired.

MarkItDown LOCK.json pins 0.1.8 at b8f79c57ebc0044be41323d89b2a45d3fda8460e and explicitly says CPython3.12 Windows x64. Its bundle contains Windows-native wheels including cryptography. Audit each wheel tag and converter-specific dependency; pure Python wheels alone may be portable, but Windows wheels and the adapter's runtime assumptions need replacement/qualification. Keep document conversion unsupported until tested on representative formats and malformed-document controls.

## Exact Linux core dependency rehearsal completed

The five baseline pins above now have staged Linux/pure-Python wheels verified against the exact PyPI release SHA-256 and size metadata. A hash-locked offline installation into a fresh Surface-only virtual environment passed on CPython 3.14.4. Native Ed25519 signing/verification, generated FIDO2 registration options, CFFI import and a blank PDF write/read roundtrip passed. The executed helper hash matches the local preserved source: 4ee78adad59580b0e6d47d4a75a205cbff68c84386beb95450e5670f2c09fba2. See work/ubuntu-rehearsal/linux-native-dependencies-result.json and requirements-linux-rehearsal.lock.

This resolves artifact availability and these dependency smoke checks only. The new isolated environment is /home/ricky-garcia/prometheus-linux-dependency-rehearsal-20261023a. The existing Surface worker and system environment were unchanged.

## Pinned source and locked startup rehearsal

The separately staged dev55 overlay records 213 included files and 826 omitted platform/dependency files. Its archive SHA-256 is d42304c98442d80ebefad32592fa2969341937eec9767a0c1b4495952a7d9f42, bound to installed manifest f093dcd0d45ee0efeb2d9135596f6a255e1c488f48818e69c6a49a0e6ba0fffa. The Surface reverified all 213 source hashes, compiled 175 Python files and imported the CLI, owner access, approval, memory and UI server modules successfully.

The real Interface and Server classes then started twice on ephemeral loopback ports with fresh isolated XDG state/data and a required owner gate. Both HTTP requests returned the locked page, neither instance had a job, and both servers and executors shut down. This is constructor/HTTP restart evidence with generated data, not real passkey enrollment or a production service installation. No model or worker was invoked. Actual owner login, authenticated review/history/undo, model inference, voice, conversion, full process restart and portable boot remain unqualified. The original Windows deployment was unchanged.
