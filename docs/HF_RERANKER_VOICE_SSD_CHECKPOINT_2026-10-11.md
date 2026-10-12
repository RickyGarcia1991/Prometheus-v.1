# Prometheus Ubuntu — Hugging Face reranking, offline voice and SSD checkpoint

October 11, 2026. **Dell remains the primary source of truth.** This Ubuntu checkout is a portable secondary environment.

## Finished and verified

### Hugging Face CPU reranker
- Original publisher: https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2
- Pinned upstream repo commit: `233902d25c440f23af6f7d6e94d2946bac0bee0a`; Apache-2.0.
- Local CPU-optimized ONNX model `onnx/model_quint8_avx2.onnx` (~23 MB), plus tokenizer and config files. All five have immutable, source-verified and code-pinned SHA-256 hashes.
- Python extras: `pip install -e '.[local-reranker]'` on Ubuntu only; pinned onnxruntime 1.31.0, tokenizers 0.23.3.
- The Ubuntu `prometheus-ui.service` has `PROMETHEUS_RERANKER=ms-marco-minilm-l6-v2`. Original Dell default remains unchanged.
- Reranks up to 12 already-retrieved document passages using no cloud API. Returns the five most relevant with original document SHA-256, source URL/date and passage metadata. Model scores are **relative**, not probabilities.
- No GPU/capture or shell processes; two CPU threads, max 256 tokens per query-passage pair. Low-memory gate and failed runtime fall back to the original cited first-stage search results.
- Synthetic three-document smoke test: three candidates, `automotive-electrical.txt` ranked first in 0.798 s; browser-workspace routing also passed.
- Code: `src/prometheus_assistant/hf_reranker.py`, `src/prometheus_assistant/ui_workspace.py`. Tests: `tests/test_hf_reranker.py`.

### Faster-Whisper Tiny English
- Converted-model publisher: https://huggingface.co/Systran/faster-whisper-tiny.en (MIT).
- Immutable upstream revision: `0d3d19a32d3338f10357c0889762bd8d64bbdeba`; **4 verified assets, 78 MB total**, in local Ubuntu user storage.
- Existing Windows `whisper.cpp` resources and SAPI remain unchanged.
- Local CTranslate2 CPU-int8; pinned faster-whisper 1.2.1, ctranslate2 4.8.2, **PyAV 16.1.0**. PyAV 19 was rejected after an API-compatibility test failed.
- `prometheus.py voice-status` and `prometheus.py transcribe /path/to/audio.wav` now work on Ubuntu using a selected 16-bit mono 16kHz PCM WAV (max 30 s, max 2.5 MB).
- Optional Ubuntu microphone recording path was implemented using `pw-record` **only after a user activates voice chat/dictation**; recording duration is 8 seconds, held in temporary storage and deleted after transcription. **Microphone capture has not yet been physically tested with the user's own voice.** No listening service or automatic capture runs.
- One CC-BY-4.0 public [LibriSpeech](https://huggingface.co/datasets/openslr/librispeech_asr) 3.5 s clip was converted temporarily and transcribed **8/8 reference words matched**. Source media not saved or committed. This is one limited test, not full speech recognition certification.
- Code: `src/prometheus_assistant/hf_voice_ubuntu.py`, `src/prometheus_assistant/voice.py`; tests `tests/test_hf_voice_ubuntu.py`.

### Curated Hugging Face shortlist
- Now **8 pinned public resources**: 7 model repositories and one evaluation dataset. Local `huggingface list` and browser Tools view identify Ollama, ONNX and CTranslate2 availability separately.
- All eight model/dataset repository HEAD revisions/licenses matched pinned metadata as checked on October 11; snapshot `docs/HF_PINNED_REVISIONS_2026-10-11_VOICE_RERANKER.json`. No automatically trusted future updates, arbitrary URLs, unsafe pickle loaders, or `trust_remote_code`.
- Other currently installed local models are Qwen3 1.7B (general), Qwen2.5-Coder 1.5B (coding), and all-MiniLM L6-v2 (document embedding).

## Portable SSD: read-only checks and quarantined code snapshot

- Mounted `/run/media/ricky-garcia/Prometheus-2TB` as NTFS (Ubuntu mount is read-write, but none of these processes **explicitly wrote to the SSD**).
- Active release from marker: **`Prometheus-v0.8.0-dev3-timing-ui-20261011`**.
- SHA-256 manifest: **610/610 files verified**, 17,727,047 bytes covered.
- RSA/SHA-256 signature mathematically verified with embedded X.509 certificate. Certificate SHA-256 fingerprint:
  `F8526536AB8526C57C68F4CC5B65CC1ABA814B99734EE24AD2FD15BB3450967C`.
- The certificate is **self-signed and delivered on the same SSD**. It has **NOT** been independently authenticated against the Dell's trusted signing identity. Do not treat the release as fully trusted yet.
- Source comparison at checkpoint: **124 identical**; **202 same-path files differ**, **284 files only on SSD**, **88 files only on Ubuntu**. Full source branch reconciliation is mandatory before a merge/upgrade.
- Quarantined, verified source-only snapshot (610 files, no personal memory, no models):
  `~/Prometheus-SSD-Staging/Prometheus-v0.8.0-dev3-timing-ui-20261011-QUARANTINE`
  with restrictive directories 0700 and file permissions 0600. These files are for review, not application activation; they can still be deliberately opened by an interpreter.
- Read-only SSD inventory: 20 Ollama model manifests, ~60 GB model store; resource presence and SQLite quick_check passed. Large models are **not** validated for Surface inference. External memory `Prometheus-Data/memory.sqlite3` was not copied, merged, restored, or modified.
- Local audit file `docs/SSD_PORTABILITY_CHECKPOINT_2026-10-11.json`. On the external SSD, the marker modification time remained unchanged during this checkpoint.

## Remaining actions before declaring portable SSD integration complete

1. Independently compare the observed certificate SHA-256 fingerprint against the **Dell's trusted release-signing certificate from its Windows certificate store** (not just a copy on the SSD).
2. Review release differences on a separate integration branch, build a compatibility map for Windows-only controller/host-agent/source/voice dependencies, and preserve rollback.
3. Run unit/integration/voice/SSD/eject and model inference tests **against the new release after porting**; merely verifying Windows binaries exist does not make them runnable on Ubuntu.
4. Back up the Dell and SSD, define explicit one-way synchronization rules for personal memory only with approval; do not merge SQLite files by replacement.
5. Validate physical USB unmount/reconnect and new-host startup with the actual SSD. Close all readers and use Ubuntu safe eject before removing it.
6. Test on-demand microphone input only when the user chooses to enable it. Prompt for the correct default input device if PipeWire reports a capture error.

## Reproducibility commands (Ubuntu only)

```bash
cd ~/Prometheus-Ubuntu
~/prometheus-env/bin/python -m pytest -q
~/prometheus-env/bin/python tools/ubuntu/hf_reranker_smoke.py
~/prometheus-env/bin/python tools/ubuntu/hf_whisper_smoke.py
~/prometheus-env/bin/python prometheus.py voice-status
~/prometheus-env/bin/python tools/ubuntu/ssd_release_signature.py --mount /run/media/ricky-garcia/Prometheus-2TB
~/prometheus-env/bin/python tools/ubuntu/compare_ssd_release.py
~/prometheus-env/bin/python prometheus.py huggingface list
git diff --check
```

No Dell, SSD controller, signing private key, USB recovery key, or external memory was modified. All Ubuntu project changes are in a separate feature branch for later review.
