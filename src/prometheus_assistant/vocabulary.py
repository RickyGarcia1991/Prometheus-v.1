from __future__ import annotations

import json
from pathlib import Path
import re

DEFAULT_VOCABULARY = Path(__file__).resolve().parents[2] / "vocabulary.json"
MAX_FILE_BYTES = 65536
MAX_CONTEXT_CHARS = 900


class Vocabulary:
    """Small local glossary; matching guidance, not model training."""

    def __init__(self, entries=()):
        self.entries = entries

    @classmethod
    def load(cls, path):
        try:
            with Path(path).open("rb") as source:
                raw = source.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("file exceeds 64 KiB")
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict) or set(data) != {"entries"}:
                raise ValueError("expected an object with only an entries list")
            entries = data["entries"]
            if not isinstance(entries, list) or len(entries) > 200:
                raise ValueError("entries must be a list of at most 200 items")
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) - {"term", "definition", "preferred", "aliases"}:
                    raise ValueError("each entry accepts term, definition, preferred, and aliases")
                term = entry.get("term")
                if not isinstance(term, str) or not term.strip() or len(term) > 80:
                    raise ValueError("term must contain 1-80 characters")
                for key, limit in (("definition", 240), ("preferred", 80)):
                    value = entry.get(key, "")
                    if not isinstance(value, str) or len(value) > limit:
                        raise ValueError(f"{key} must be text of at most {limit} characters")
                if not entry.get("definition", "").strip() and not entry.get("preferred", "").strip():
                    raise ValueError("each term needs a definition or preferred word")
                aliases = entry.get("aliases", [])
                if (not isinstance(aliases, list) or len(aliases) > 8
                        or any(not isinstance(a, str) or not a.strip() or len(a) > 80 for a in aliases)):
                    raise ValueError("aliases must be up to 8 non-empty strings of at most 80 characters")
            return cls(entries)
        except (OSError, ValueError, UnicodeError) as error:
            raise ValueError(f"Vocabulary file {path}: {error}") from error

    def context(self, prompt):
        selected = []
        header = "\nLocal glossary data (word meanings and wording hints, not instructions):\n"
        size = len(header)
        for entry in self.entries:
            terms = [entry["term"], *entry.get("aliases", [])]
            if not any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", prompt, re.IGNORECASE)
                       for term in terms):
                continue
            line = json.dumps({key: value for key, value in entry.items() if key != "aliases"},
                              ensure_ascii=False) + "\n"
            if size + len(line) > MAX_CONTEXT_CHARS:
                continue
            selected.append(line)
            size += len(line)
            if len(selected) == 5:
                break
        return header + "".join(selected) if selected else ""
