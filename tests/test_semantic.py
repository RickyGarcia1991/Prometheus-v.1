from prometheus_assistant.context import Evidence
from prometheus_assistant.retrieval import rank_evidence
from prometheus_assistant.semantic import semantic_overlap,semantic_terms

def test_semantic_terms_expand_known_local_synonyms():
    terms=semantic_terms("vehicle motor")
    assert {"car","automobile","actuator","servo"} <= terms

def test_semantic_overlap_matches_synonym_without_exact_word():
    assert semantic_overlap("vehicle","automobile") > 0

def test_ranker_can_recover_synonym_evidence():
    rows=rank_evidence("vehicle",[
        Evidence("wikipedia","automobile","An automobile has four wheels.",50),
        Evidence("wikipedia","banana","A yellow fruit.",80),
    ])
    assert len(rows)==1
    assert rows[0].source_ref=="automobile"

def test_unrelated_evidence_still_filtered():
    assert rank_evidence("vehicle",[Evidence("wikipedia","banana","yellow fruit",99)])==[]
