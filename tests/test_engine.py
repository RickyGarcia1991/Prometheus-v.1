from prometheus.cognition.stub import StubModel
from prometheus.core.engine import PrometheusEngine
from prometheus.memory.in_memory import InMemoryStore

def test_engine_round_trip():
    engine = PrometheusEngine(StubModel(), InMemoryStore())
    reply = engine.respond("hello")
    assert "hello" in reply
