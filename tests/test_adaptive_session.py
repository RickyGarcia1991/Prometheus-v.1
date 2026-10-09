from prometheus_assistant import cli
from prometheus_assistant.memory import MemoryStore
from prometheus_assistant.hardware import HardwareProfile


def test_busy_chat_retains_memory_commands_and_recovers(monkeypatch,tmp_path,capsys):
    class Client:
        base_url='http://127.0.0.1:11434';model='llama3.2:1b'
        def local_models(self):return [{'name':self.model}]
        def resident_memory_gib(self):return 0
    monkeypatch.setattr(cli,'detect_hardware',lambda:HardwareProfile(8,8,'Windows',.5))
    prompts=iter(['hello','/remember preference | color | blue','/memories','/exit'])
    monkeypatch.setattr(cli,'_console_input_or_shutdown',lambda *args:next(prompts))
    with MemoryStore(tmp_path/'memory.db') as memory:
        session=memory.create_session('llama3.2:1b')
        assert cli.interactive_chat(memory,Client(),session,adaptive=True)==0
        assert memory.knowledge()[0]['value']=='blue'
        assert memory.history(session)==[]
    captured=capsys.readouterr()
    assert 'available RAM' in captured.err and 'blue' in captured.out


def test_auto_resume_allows_host_change_and_preserves_history(tmp_path):
    with MemoryStore(tmp_path/'memory.db') as memory:
        sid=memory.create_session('gpt-oss:20b')
        memory.save_exchange(sid,'Hello','Hi')
        assert cli.selected_session(memory,sid,'llama3.2:1b',True)==sid
        memory.record_model_use(sid,'llama3.2:1b',2048,2)
        assert len(memory.history(sid))==2
        assert memory.db.execute('SELECT model FROM model_events').fetchone()[0]=='llama3.2:1b'
        assert memory.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
