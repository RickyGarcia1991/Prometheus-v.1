from prometheus_assistant.release_gate import ReleaseEvidence,evaluate_release

def good(**changes):
    data=dict(tests_passed=230,tests_failed=0,diff_clean=True,integrity_ok=True,
              rollback_ready=True,hygiene_ok=True,benchmark_failed=0)
    data.update(changes)
    return ReleaseEvidence(**data)

def test_release_passes_only_with_all_gates():
    assert evaluate_release(good())["passed"]

def test_hygiene_failure_blocks_release():
    out=evaluate_release(good(hygiene_ok=False))
    assert not out["passed"] and "repository hygiene failed" in out["reasons"]

def test_benchmark_failure_blocks_release():
    out=evaluate_release(good(benchmark_failed=2))
    assert not out["passed"] and "acceptance benchmark has 2 failure(s)" in out["reasons"]

def test_existing_change_gate_failure_is_preserved():
    out=evaluate_release(good(tests_failed=1))
    assert not out["passed"]
    assert any("tests failed" in reason for reason in out["reasons"])
