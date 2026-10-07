from prometheus_assistant.evaluation import Evaluation, evaluate_change

def test_clean_change_passes_gate():
    g=evaluate_change(Evaluation(180))
    assert g.passed and not g.reasons

def test_failure_conditions_block_change():
    g=evaluate_change(Evaluation(179,1,False,False,False))
    assert not g.passed
    assert "1 tests failed" in g.reasons
    assert "diff validation failed" in g.reasons
    assert "integrity validation failed" in g.reasons
    assert "rollback checkpoint unavailable" in g.reasons

def test_zero_tests_cannot_be_promoted():
    g=evaluate_change(Evaluation(0))
    assert not g.passed and "no passing tests recorded" in g.reasons
