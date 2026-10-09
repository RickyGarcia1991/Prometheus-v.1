

from prometheus_assistant.ollama import OllamaClient


def test_context_size_is_configurable():
    client=OllamaClient("http://127.0.0.1:11434","llama3.2:1b",num_ctx=3072)
    assert client.num_ctx==3072


def test_context_size_rejects_unsafe_values():
    import pytest
    with pytest.raises(ValueError):
        OllamaClient("http://127.0.0.1:11434","llama3.2:1b",num_ctx=128)


def test_adaptive_cpu_budget_is_sent_to_runtime(monkeypatch):
    client = OllamaClient(num_ctx=4096, num_thread=6)
    sent = []
    def request(path, payload):
        sent.append(payload)
        return {"done": True, "message": {"content": "ready"}}
    monkeypatch.setattr(client, "_request", request)
    client.chat([{"role": "user", "content": "test"}])
    assert sent[0]["options"]["num_thread"] == 6
    assert sent[0]["options"]["num_ctx"] == 4096


def test_reasoning_model_has_room_for_final_answer(monkeypatch):
    client = OllamaClient(model="gpt-oss:20b")
    sent = []
    def request(path, payload):
        sent.append(payload)
        return {"done": True, "message": {"content": "ready", "thinking": "private reasoning"}}
    monkeypatch.setattr(client, "_request", request)
    assert client.chat([{"role": "user", "content": "test"}])[0] == "ready"
    assert sent[0]["think"] == "low"
    assert sent[0]["options"]["num_predict"] >= 2048


def test_truncated_answer_is_not_accepted(monkeypatch):
    import pytest
    from prometheus_assistant.ollama import LocalModelError
    client = OllamaClient()
    monkeypatch.setattr(client, "_request", lambda *args: {"done": True, "done_reason": "length", "message": {"content": "partial"}})
    with pytest.raises(LocalModelError, match="incomplete"):
        client.chat([])


def test_idle_model_release_adapts_to_small_and_busy_hosts(monkeypatch):
    from prometheus_assistant.hardware import HardwareProfile
    client = OllamaClient()
    sent = []
    def request(path, payload):
        sent.append(payload)
        return {"done": True, "message": {"content": "Complete answer"}}
    monkeypatch.setattr(client, "_request", request)
    cases = [(8, 5, "30s"), (32, 1, "30s"), (32, 12, "5m"), (64, None, "30s")]
    for total, available, expected in cases:
        monkeypatch.setattr("prometheus_assistant.hardware.detect_hardware",
                            lambda: HardwareProfile(total, 8, "Windows", available))
        assert client.chat([{"role": "user", "content": "Hello"}])[0] == "Complete answer"
        assert sent[-1]["keep_alive"] == expected
