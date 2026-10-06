# Local vocabulary glossary

Prometheus automatically loads vocabulary.json beside prometheus.py when starting chat, ask, or doctor. Edit it with a text editor, save, then restart chat. This file guides wording and explains custom terms; it does not retrain the model or guarantee correct answers.

Each entry has a term and a definition or preferred word. Optional aliases let abbreviations match the term. Example:

```json
{
  "entries": [
    {
      "term": "utilize",
      "preferred": "use",
      "definition": "Prefer the simpler word use when it preserves the meaning."
    },
    {
      "term": "diagnostic trouble code",
      "aliases": ["DTC"],
      "definition": "A code stored when a vehicle detects a fault."
    }
  ]
}
```

Only terms matching the current question (case-insensitive whole terms or phrases) are sent. At most five entries and 900 characters are added. Prior chat context is trimmed to keep the overall 6000-character budget. An inflected form such as DTCs needs its own alias.

Limits: 64 KiB file, 200 entries, 80 characters per term/preferred word/alias, 240 per definition, and eight aliases per entry. The file must be valid UTF-8 JSON with an entries list. Invalid files produce an actionable error. A missing default file simply disables the glossary; a missing explicitly selected file is an error.

From this folder:

```cmd
START_PROMETHEUS.cmd doctor
START_PROMETHEUS.cmd --vocabulary "C:\path\my-words.json" chat
START_PROMETHEUS.cmd --no-vocabulary chat
```

Place global options before chat/ask/doctor. Saved sessions/history can be read without the glossary or Ollama. The model still has no internet access or executable tools.

Prefer a short glossary of terms you actually use over loading a complete general-purpose dictionary. Definitions and preferred words should be plain data, not commands. The six starter entries cover plain-language alternatives, vocabulary dictionaries, and the Prometheus project.
