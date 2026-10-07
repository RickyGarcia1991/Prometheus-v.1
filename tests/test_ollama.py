

from prometheus_assistant.ollama import OllamaClient


def test_context_size_is_configurable():
    client=OllamaClient("http://127.0.0.1:11434","llama3.2:1b",num_ctx=3072)
    assert client.num_ctx==3072


def test_context_size_rejects_unsafe_values():
    import pytest
    with pytest.raises(ValueError):
        OllamaClient("http://127.0.0.1:11434","llama3.2:1b",num_ctx=128)
