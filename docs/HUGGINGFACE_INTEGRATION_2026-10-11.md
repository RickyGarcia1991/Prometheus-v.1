# Hugging Face → Prometheus: portable Ubuntu integration

**October 11 update:** The earlier list of seven curated resources and the deferred Whisper/reranker status are superseded by [the reranker, offline voice, and SSD checkpoint](HF_RERANKER_VOICE_SSD_CHECKPOINT_2026-10-11.md): **8 pinned repos**, both CPU runtimes installed and tested on Ubuntu; SSD core source staged for review only.
October 11, 2026. Dell is the primary Prometheus computer; Ubuntu Surface Laptop Go is the portable secondary. SSD/security flash key unchanged.

## Delivered and verified

### Lightweight meaning-based document search
- Installed `all-minilm:l6-v2` (~45 MB Ollama, Apache-2.0) from the [all-MiniLM-L6-v2 model family](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
- 384-dimensional embeddings; Ollama model digest `1b226e2802dbb772b5fc32a58f103ca1804ef7501331012de126ab22f67475ef`.
- Ubuntu Prometheus user service sets `PROMETHEUS_EMBEDDING_MODEL=all-minilm:l6-v2`; Dell's default remains unchanged. Unsupported embedding names are rejected.
- Model context capped at 512 tokens with truncation disabled; indexing defers under the existing 2.75 GiB free-memory gate.
- The existing DocumentLibrary retains original document SHA-256, versions, source URL/date and individual embedding identity. It does not silently rewrite previous indexes.
- Real synthetic smoke test: 3 temporary passages indexed, automobile electrical passage ranked first for a paraphrased starting-problem query; hybrid retrieval and citations working; ~0.325 seconds. No personal documents imported.

### Coding helper
- Installed `qwen2.5-coder:1.5b` (~986 MB quantized Ollama, Apache-2.0), family [Qwen/Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct).
- Ollama digest `d7372fd828518a4d38b1eb196c673c31a85f2ed302b3d1e406c4c2d1b64a0668`.
- Existing Prometheus routing selected the local coder for a Python coding task. Generated code passed AST syntax, signature and basic no-import/no-unsafe-call checks; code was NOT executed, and functional correctness is NOT proven.
- Existing general model `qwen3:1.7b` remains the conversation default.

### Attributed evaluation data
- Pulled 16 ARC-Challenge validation questions from [allenai/ai2_arc](https://huggingface.co/datasets/allenai/ai2_arc); CC-BY-SA-4.0, pinned repo commit `210d026faf9955653af8916fad021475a3f00453`.
- Stored privately at `~/.local/share/Prometheus/HuggingFace/Evaluations/ai2_arc_challenge_validation_sample.json` with checksum sidecar: `db3344758a0246d151fff3cd90a5523c6abc929ec5eec79d310e6315787b124c`.
- Same six questions: free-format 4/6, 2 format errors in 10.338 seconds; Ollama JSON schema 6/6, 0 format errors in 9.725 seconds. Small nonrepresentative sample; indicates improved output validity, not broad accuracy.
- These examples are not ingested as factual memory or training data; offline benchmark contacts only local Ollama.

### Local Hugging Face resource registry
- Seven pinned Hugging Face repositories, six models and one dataset, with source commit, license, task role and risk notes.
- All seven upstream revisions and licenses were checked against the current Hugging Face API; snapshot: `docs/HF_PINNED_REVISIONS_2026-10-11.json`. Repo SHA and quantized Ollama artifact SHA differ by design and are not claimed byte-identical.
- Curated index: `src/prometheus_assistant/hf_curated_catalog.json`; query functions: `src/prometheus_assistant/hf_resources.py`.
- New read-only Prometheus Core tool: `knowledge/huggingface`. Browser **Tools** mode shows the vetted shortlist. Local list/status requires no internet.
- No remote code, arbitrary repo URL, token, dynamic model loading, automatic training, or background repository updates are enabled.

## Ubuntu commands

```bash
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py huggingface list
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py huggingface list --role semantic-memory
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py huggingface list --search Coder
# Explicit online metadata comparison of one allowlisted publisher:
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py huggingface compare --repo sentence-transformers/all-MiniLM-L6-v2
# Synthetic local tests and private dataset validation:
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/tools/ubuntu/hf_document_smoke.py
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/tools/ubuntu/hf_coder_smoke.py
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/tools/ubuntu/hf_arc_benchmark.py status
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/tools/ubuntu/hf_arc_benchmark.py benchmark --questions 6 --structured
```

Import only explicitly chosen authorized files through the Prometheus browser document workspace. Click **Index missing embeddings** when available. Nothing imports whole personal folders or SSD contents automatically.

## Useful candidates not automatically installed

| Hugging Face repo | Purpose | Priority |
|---|---|---|
| [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | Larger/multilingual semantic retrieval | Compare on Dell first; reindex if selected |
| [MS MARCO MiniLM](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) | Re-rank the first-stage search hits | Separate ONNX/PyTorch runtime and CPU validation |
| [Whisper tiny.en](https://huggingface.co/openai/whisper-tiny.en) | Offline voice transcription | Pending Ubuntu audio/permissions integration |
| [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | Ultra-light chat fallback | Compare quality first |
| [PEFT / LoRA](https://huggingface.co/docs/peft/en/methods/overview) | Task-specific model adaptation | Defer until provenance and evaluation baselines stabilize |

Official supporting documentation: [quantized GGUF through Ollama](https://huggingface.co/docs/hub/en/ollama), [safetensors](https://huggingface.co/docs/safetensors/index), [secure selective revision downloads](https://huggingface.co/docs/huggingface_hub/main/en/package_reference/file_download), [dataset loading](https://huggingface.co/docs/datasets/loading), [model evaluation](https://huggingface.co/docs/evaluate/index).

## Limits and rollback

- Original model author licenses are reviewed; this does NOT establish that all downstream quantizations or runtime variants are identical to upstream files.
- Hugging Face warns that pickle weights can contain executable content. No unreviewed pickle checkpoints or `trust_remote_code=True` models were loaded.
- Strict schemas constrain output format, not correctness of answers.
- Removing `PROMETHEUS_EMBEDDING_MODEL=all-minilm:l6-v2` from the Ubuntu user's `prometheus-ui.service`, then reloading/restarting the service, returns the previous default embedding model; that original default may need to be installed for indexing.
- Dell, SSD and security flash key are unchanged; cross-host port remains gated on signed release, backup, reconciliation and rollback testing.
