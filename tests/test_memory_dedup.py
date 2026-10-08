from prometheus_assistant.memory import MemoryStore


def test_exact_repeated_knowledge_is_idempotent(tmp_path):
    with MemoryStore(tmp_path / 'memory.db') as store:
        kw = dict(source_type='user_statement', source_ref='turn:1', retention='until_replaced')
        first = store.remember('preference', 'answer.length', 'short', **kw)
        second = store.remember('preference', 'answer.length', 'short', **kw)
        assert first == second
        assert len(store.knowledge()) == 1
        replacement = store.remember('preference', 'answer.length', 'detailed', **kw)
        assert replacement != first
        assert len(store.knowledge()) == 1
        assert len(store.knowledge(include_superseded=True)) == 2


def test_changed_confidence_is_not_silently_deduplicated(tmp_path):
    with MemoryStore(tmp_path / 'memory.db') as store:
        kw = dict(source_type='user_statement', source_ref='turn:1', retention='until_replaced')
        store.remember('fact', 'actuator', 'electric', confidence=0.6, **kw)
        store.remember('fact', 'actuator', 'electric', confidence=0.9, **kw)
        assert len(store.knowledge(include_superseded=True)) == 2
