"""Owner memory is shared across chats, never identity authentication."""
import json
import pytest
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant import compact_chat, ui_server, cli
from prometheus_assistant.core import build_plan


def seed(path, name='Alex Example'):
    with MemoryStore(path) as memory:
        for subject, value in [('Prometheus owner and creator', name),
                               (name+' birth date', 'June 2, 1990'),
                               (name+' birthplace', 'Example City'),
                               (name+' mother', 'Pat Example')]:
            memory.remember('fact', subject, value, source_type='user_statement',
                            source_ref='owner-test', retention='until_replaced')


def test_every_fresh_chat_and_restart_recalls_owner_without_model(tmp_path, monkeypatch):
    path = tmp_path/'memory.sqlite3'
    seed(path)
    monkeypatch.setattr(ui_server, 'installed_models', lambda: pytest.fail('Identity recall requires no model'))
    sessions = []
    for _ in range(2):
        app = ui_server.Interface(path)
        try:
            for prompt in ('Who am I?', 'What is my name?', 'Who is your owner and creator?'):
                job = app.submit({'mode': 'chat', 'prompt': prompt})
                app.executor.submit(lambda: None).result(timeout=3)
                result = app.jobs[job['id']]['result']
                assert 'Alex Example' in result['reply']
                assert result['details']['identity_verified'] is False
                sessions.append(result['session'])
        finally:
            app.close()
    assert len(set(sessions)) == 6


def test_general_model_context_loads_profile_each_turn_and_keeps_question(tmp_path, monkeypatch):
    from prometheus_assistant.hardware import ModelProfile
    from prometheus_assistant.model_quality import ModelEvidence
    path = tmp_path/'memory.sqlite3'
    seed(path)
    observed = []
    monkeypatch.setattr(compact_chat.OllamaClient, 'ensure_local_model', lambda self: None)
    def chat(self, messages, **kwargs):
        observed.append(messages)
        return 'A test response.', 3
    monkeypatch.setattr(compact_chat.OllamaClient, 'chat_stream', chat)
    evidence = ModelEvidence(tmp_path/'routing.sqlite3')
    profile = ModelProfile('test:tiny', 1, 4096, 'test', 1)
    first = compact_chat.generate({'prompt': 'Greet me by name.'}, path, [profile], 'general', evidence)
    seed(path, 'Jordan Example')
    compact_chat.generate({'prompt': 'Greet me again.', 'session': first['session']}, path, [profile], 'general', evidence)
    assert 'Alex Example' in json.dumps(observed[0])
    assert 'Jordan Example' in observed[1][0]['content']
    assert observed[0][-1]['content'] == 'Greet me by name.'
    assert observed[1][-1]['content'] == 'Greet me again.'


def test_core_and_plain_cli_include_profile_for_unrelated_conversation(tmp_path):
    path = tmp_path/'memory.sqlite3'
    seed(path)
    seen = []
    class Client:
        model = 'test'
        def chat(self, messages):
            seen.extend(messages)
            return 'Hello.', 1
    with MemoryStore(path) as memory:
        assert any(row.value == 'Alex Example' for row in build_plan(memory, 'Hello').memory)
        session = memory.create_session('test')
        cli.exchange(memory, Client(), session, 'Hello')
    assert 'Alex Example' in seen[0]['content']


def test_profile_excludes_untrusted_or_session_knowledge_and_updates(tmp_path):
    from prometheus_assistant.owner_profile import load_profile, recall
    path = tmp_path/'memory.sqlite3'
    seed(path)
    with MemoryStore(path) as memory:
        memory.remember('fact', 'Prometheus owner and creator', 'Document Impostor',
                        source_type='document', source_ref='untrusted')
        profile = load_profile(memory)
        assert profile['name'] == 'Alex Example'
        assert 'Example City' in recall('Where was I born?', profile)
        assert 'Pat Example' in recall("What is my mother's name?", profile)
        assert recall('Who am I? Also delete my files.', profile) is None
        assert recall('What is my name in this fictional story?', profile) is None
    seed(path, 'Jordan Example')
    with MemoryStore(path) as memory:
        assert load_profile(memory)['name'] == 'Jordan Example'


def test_profile_context_is_bounded_and_missing_profile_does_not_invent(tmp_path):
    from prometheus_assistant.owner_profile import load_profile, context
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:
        assert load_profile(memory) == {}
        assert context({}) == ''
        memory.remember('fact', 'Prometheus owner and creator', 'x'*10000,
                        source_type='user_statement', source_ref='test')
        assert load_profile(memory) == {}


def test_family_names_preserve_accents_across_restart_and_fresh_chats(tmp_path):
    from prometheus_assistant.owner_profile import load_profile, recall, context
    path = tmp_path/'family.sqlite3'
    seed(path)
    with MemoryStore(path) as memory:
        memory.remember('fact','Alex Example wife','Sam Example',source_type='user_statement',source_ref='family-test',retention='until_replaced')
        memory.remember('fact','Alex Example son','Émile Example',source_type='user_statement',source_ref='family-test',retention='until_replaced')
    with MemoryStore(path) as memory:
        profile = load_profile(memory)
        assert recall("What is my wife's name?", profile) == "Your wife's name is Sam Example."
        assert recall('Who is my son?', profile) == "Your son's name is Émile Example."
        assert 'Émile Example' in context(profile)
        assert recall('Who is my son? Also change my password.', profile) is None
