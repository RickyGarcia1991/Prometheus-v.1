import pytest
from prometheus_assistant.context import Evidence
from prometheus_assistant.retrieval import rank_evidence, tokens

def test_tokens_normalize_and_ignore_short_words():
    assert tokens("The ROBOT uses AI actuators.")=={"the","robot","uses","actuators"}

def test_relevant_evidence_beats_unrelated_high_priority():
    rows=rank_evidence("robot actuator",[
        Evidence("memory","paint","The car is blue.",80),
        Evidence("wikipedia","robot actuator","A robot actuator moves a joint.",50),
    ])
    assert rows[0].source_ref=="robot actuator"

def test_source_weight_breaks_equal_relevance_toward_memory():
    rows=rank_evidence("project decision",[
        Evidence("wikipedia","project decision","project decision",50),
        Evidence("memory","project decision","project decision",50),
    ])
    assert rows[0].source_type=="memory"

def test_limit_validation():
    with pytest.raises(ValueError):
        rank_evidence("x",[],0)
