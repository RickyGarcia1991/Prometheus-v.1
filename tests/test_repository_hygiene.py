from pathlib import Path

def test_hygiene_checks_forbidden_payloads_and_secrets():
    text=(Path(__file__).parents[1]/"tools"/"CHECK-REPOSITORY-HYGIENE.ps1").read_text(encoding="utf-8")
    assert "memory\\.sqlite3" in text
    assert "\\.(zim|gguf|bin)" in text
    assert "PRIVATE KEY" in text
    assert "sk-proj-" in text
    assert "AKIA" in text
