from prometheus_assistant.worker_policy import DEFAULT_CODE_WORKER_CONTRACT, EvalEvidence


def test_code_worker_contract_is_offline_and_credential_free():
    c=DEFAULT_CODE_WORKER_CONTRACT
    assert c.baseline_required and c.static_check_required and c.final_tests_required
    assert c.real_credentials_allowed is False
    assert c.network_by_default is False


def test_eval_perfect_tool_match_passes():
    e=EvalEvidence(True,("inspect","test"),("inspect","test"),True,True)
    assert e.tool_accuracy==1.0
    assert e.passed


def test_eval_extra_tool_lowers_accuracy():
    e=EvalEvidence(True,("inspect",),("inspect","shell"),True,True)
    assert e.tool_accuracy==0.5
    assert not e.passed


def test_eval_authorization_failure_fails_even_if_task_completed():
    e=EvalEvidence(True,(),(),False,True)
    assert not e.passed
