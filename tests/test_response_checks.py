from types import SimpleNamespace
from prometheus_assistant.response_checks import output_issues
from prometheus_assistant.self_evaluation import evaluate_agent_result
from prometheus_assistant.agent import run_agent
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.tool_registry import ToolRegistry

PROMPT="What is 17 plus 25? Answer with the number and a one-sentence explanation."

def test_observed_bad_answer_cannot_pass():
    core=SimpleNamespace(reply="17 + 25 = 42\n17 + 25 = 42",evidence=(),plan=SimpleNamespace(prompt=PROMPT),evaluation=SimpleNamespace(authorization_ok=True,tool_accuracy=1.0,passed=True))
    result=evaluate_agent_result(core)
    assert not result.passed
    assert "requested explanation is missing" in result.reasons
    assert result.factual_accuracy=="not_verified"

def test_requested_counts_and_json():
    assert output_issues("List three reasons", "1. A\n2. B")
    assert not output_issues("List three reasons", "1. A\n2. B\n3. C")
    assert output_issues("Return only JSON", "```json\n{}\n```")
    assert not output_issues("Return only JSON", '{"answer":42}')

def test_bounded_response_repair_without_tools(tmp_path):
    class Model:
        calls=0
        def chat(self,messages):
            self.calls+=1
            assert self.calls<=2
            return ("42\n42" if self.calls==1 else "42 — adding 17 and 25 gives 42."),1
    model=Model()
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        result=run_agent(memory,model,ToolRegistry(),PROMPT)
    assert model.calls==2 and result.self_evaluation.passed
    assert result.core.evidence==()

def test_failed_repair_stays_flagged(tmp_path):
    class Model:
        calls=0
        def chat(self,messages):
            self.calls+=1
            assert self.calls<=2
            return "42\n42",1
    model=Model()
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        result=run_agent(memory,model,ToolRegistry(),PROMPT)
    assert model.calls==2 and not result.self_evaluation.passed
