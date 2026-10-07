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

def test_source_diversity_prevents_single_source_crowding():
    rows=rank_evidence("robot actuator",[
        Evidence("memory","m1","robot actuator one",90),
        Evidence("memory","m2","robot actuator two",89),
        Evidence("memory","m3","robot actuator three",88),
        Evidence("wikipedia","w1","robot actuator reference",60),
    ],limit=4,max_per_source=2)
    assert [r.source_type for r in rows].count("memory")==2
    assert any(r.source_type=="wikipedia" for r in rows)

def test_query_coverage_rewards_broader_match():
    rows=rank_evidence("robot hydraulic actuator",[
        Evidence("wikipedia","robot","robot system",50),
        Evidence("wikipedia","hydraulic actuator","robot hydraulic actuator joint",50),
    ])
    assert rows[0].source_ref=="hydraulic actuator"

def test_source_limit_validation():
    with pytest.raises(ValueError):
        rank_evidence("robot",[],max_per_source=0)
