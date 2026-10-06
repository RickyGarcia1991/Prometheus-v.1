from pathlib import Path
import json
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from prometheus_assistant.vocabulary import Vocabulary

def write_vocab(tmp_path, entries):
    path = tmp_path / "words.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path

def test_matching_aliases_and_unicode_are_whole_terms(tmp_path):
    vocab = Vocabulary.load(write_vocab(tmp_path, [
        {"term": "diagnostic trouble code", "aliases": ["DTC"], "definition": "A code stored when a vehicle detects a fault."},
        {"term": "café", "definition": "A coffee shop."},
    ]))
    assert "diagnostic trouble code" in vocab.context("Explain DTC")
    assert "coffee shop" in vocab.context("What is a café?")
    assert vocab.context("Explain DTCs and cafeteria") == ""

def test_preferred_word_and_only_relevant_entries(tmp_path):
    vocab = Vocabulary.load(write_vocab(tmp_path, [
        {"term": "utilize", "preferred": "use", "definition": "Use is simpler."},
        {"term": "unrelated", "definition": "Should not be sent."},
    ]))
    context = vocab.context("What does utilize mean?")
    assert "use" in context and "unrelated" not in context

def test_context_is_bounded(tmp_path):
    vocab = Vocabulary.load(write_vocab(tmp_path, [
        {"term": f"term{i}", "definition": "x" * 240} for i in range(10)
    ]))
    assert len(vocab.context(" ".join(f"term{i}" for i in range(10)))) <= 900

@pytest.mark.parametrize("value", [
    {"entries": "wrong"}, {"entries": [{"term": ""}]},
    {"entries": [{"term": "x", "definition": 7}]},
    {"entries": [{"term": "x", "aliases": [""]}]},
    {"entries": [{"term": "x", "unknown": "bad"}]},
])
def test_invalid_configuration_is_actionable(tmp_path, value):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="Vocabulary"):
        Vocabulary.load(path)

def test_missing_explicit_file_and_oversized_file(tmp_path):
    with pytest.raises(ValueError, match="Vocabulary"):
        Vocabulary.load(tmp_path / "missing.json")
    path = tmp_path / "big.json"
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="Vocabulary"):
        Vocabulary.load(path)
