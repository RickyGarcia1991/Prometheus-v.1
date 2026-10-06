# Live evaluation — October 4, 2026

Eight synthetic tasks exercised real llama3.2:1b in fresh CLI processes with an isolated
database. Seven of eight narrow rubrics passed: arithmetic, time conversion, supplied-note
budget extraction, simpler vocabulary, unavailable private information, numbered steps and
fresh-process recall. Initial exact-code response failed: amber-731 became 731. Subsequent
selected-session recall correctly returned amber-731.

Median model latency 1.165 seconds; range 0.46–7.53 seconds. Whole-process times included
startup. Ollama reported about 1.45 GB model allocation and zero VRAM; this is not peak
process RAM or sustained-performance evidence.

The planning response passed formatting but gave weak operational advice, suggesting cloud
storage and system restore rather than precise offline project recovery. Use RECOVERY.md
instead of generated instructions. Eight cases are not a general accuracy score.

Repeat tools/evaluate.py after changing model, prompt or hardware; manually review content
alongside metrics. Never include private conversations in evaluation reports.
