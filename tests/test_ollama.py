

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


def test_streaming_idle_residency_matches_memory_policy(monkeypatch):
    import io
    import json
    from prometheus_assistant.hardware import HardwareProfile

    sent = []
    class Socket:
        def setblocking(self, value):
            pass
        def shutdown(self, value):
            pass
    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = Socket()
        def connect(self):
            pass
        def request(self, method, path, body, headers):
            sent.append(json.loads(body))
        def getresponse(self):
            body = json.dumps({'message': {'role': 'assistant', 'content': 'Ready.'}, 'done': True}).encode()+b'\n'
            response = io.BytesIO(body)
            response.status = 200
            return response
        def close(self):
            pass
    client = OllamaClient()
    monkeypatch.setattr('http.client.HTTPConnection', Connection)
    for total, available, expected in [(8, 5, '30s'), (16, 1, '30s'), (16, 6, '5m'), (64, None, '30s')]:
        monkeypatch.setattr('prometheus_assistant.hardware.detect_hardware',
                            lambda: HardwareProfile(total, 8, 'Windows', available))
        assert client.chat_stream([{'role': 'user', 'content': 'Hello'}])[0] == 'Ready.'
        assert sent[-1]['keep_alive'] == expected


def test_shared_output_budget_matches_reasoning_model_minimum():
    from prometheus_assistant.ollama import output_token_budget
    assert output_token_budget('qwen3:1.7b') == 384
    assert output_token_budget('qwen3:1.7b', 128) == 128
    assert output_token_budget('gpt-oss:20b', 384) == 2048
    assert output_token_budget('gpt-oss:20b', 4096) == 4096


def stream_events(monkeypatch, events):
    """Script the HTTP response boundary; no runtime or model is contacted."""
    import io
    import json
    class Socket:
        def setblocking(self, value):
            pass
        def shutdown(self, value):
            pass
    class Connection:
        debuglevel = 0
        def __init__(self, *a, **kw):
            self.sock = Socket()
        def connect(self):
            pass
        def request(self, *a, **kw):
            pass
        def getresponse(self):
            response = io.BytesIO(b''.join(json.dumps(event).encode()+b'\n' for event in events))
            response.status = 200
            return response
        def close(self):
            pass
    monkeypatch.setattr('http.client.HTTPConnection', Connection)


def test_output_limit_retains_valid_final_timings_but_still_rejects_answer(monkeypatch):
    import pytest
    from prometheus_assistant.ollama import LocalModelError
    stream_events(monkeypatch, [{'message': {'content': 'Incomplete answer'}, 'done': True, 'done_reason': 'length',
        'load_duration': 45_000_000_000, 'prompt_eval_duration': 7_000_000_000, 'eval_duration': 4_000_000_000,
        'prompt_eval_count': 937, 'eval_count': 384}])
    client = OllamaClient(model='qwen3:1.7b', num_ctx=4096)
    with pytest.raises(LocalModelError, match='output limit'):
        client.chat_stream([{'role': 'user', 'content': 'Synthetic public test.'}])
    assert client.last_metrics['load_seconds'] == 45
    assert client.last_metrics['prompt_eval_seconds'] == 7
    assert client.last_metrics['eval_seconds'] == 4
    assert client.last_metrics['eval_count'] == 384
    assert client.last_metrics['prompt_eval_count'] == 937
    assert client.last_metrics['context'] == 4096
    assert client.last_metrics['model'] == 'qwen3:1.7b'
    assert 'Incomplete answer' not in str(client.last_metrics)


def test_reused_client_does_not_attribute_previous_success_metrics_to_interrupted_stream(monkeypatch):
    import pytest
    from prometheus_assistant.ollama import LocalModelError
    client = OllamaClient()
    stream_events(monkeypatch, [{'message': {'content': 'Complete'}, 'done': True, 'load_duration': 1_000_000_000}])
    client.chat_stream([])
    assert client.last_metrics['load_seconds'] == 1
    stream_events(monkeypatch, [{'message': {'content': 'Partial'}, 'done': False}])
    with pytest.raises(LocalModelError, match='complete answer'):
        client.chat_stream([])
    assert client.last_metrics == {}


def test_missing_or_invalid_optional_metrics_are_not_reported_as_zero_or_numbers(monkeypatch):
    stream_events(monkeypatch, [{'message': {'content': 'Complete'}, 'done': True,
        'prompt_eval_duration': float('nan'), 'eval_duration': -1, 'prompt_eval_count': True, 'eval_count': 'many'}])
    client = OllamaClient()
    assert client.chat_stream([])[0] == 'Complete'
    assert not {'load_seconds', 'prompt_eval_seconds', 'eval_seconds', 'eval_count', 'prompt_eval_count'} & client.last_metrics.keys()


def test_invalid_explicit_load_timing_cannot_be_saved_as_failed_attempt_metrics(monkeypatch):
    import pytest
    from prometheus_assistant.ollama import LocalModelError
    client = OllamaClient()
    for value in (None, True, -1, float('nan'), float('inf'), 10**40, '45'):
        stream_events(monkeypatch, [{'message': {'content': 'Partial'}, 'done': True,
                                   'done_reason': 'length', 'load_duration': value}])
        with pytest.raises(LocalModelError):
            client.chat_stream([])
        assert client.last_metrics == {}
