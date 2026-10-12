import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from prometheus_assistant import compact_chat, model_routing, vision
from prometheus_assistant.ollama import GenerationCancelled, LocalModelError


PNG = b'\x89PNG\r\n\x1a\n' + b'example'
ENCODED = base64.b64encode(PNG).decode('ascii')


@pytest.fixture
def runtime(monkeypatch):
    calls = []
    state = {'models': [{'name': 'qwen3-vl:2b'}], 'cancel_during': False}
    hardware = SimpleNamespace(available_ram_gib=8)
    monkeypatch.setattr(vision, 'detect_hardware', lambda: hardware)
    monkeypatch.setattr(compact_chat, 'detect_hardware', lambda: hardware)
    profile = SimpleNamespace(model='qwen3-vl:2b', context=4096, cpu_threads=2)
    def choose(installed, host, role, resident):
        calls.append(('choose', installed, role))
        return profile if profile.model in installed else None
    monkeypatch.setattr(model_routing, 'choose_helper', choose)
    class Client:
        def __init__(self, base_url, model, **kwargs):
            calls.append(('client', model, kwargs))
            self.model = model
            self.last_metrics = {'eval_count': 12, 'seconds': .1}
        def local_models(self):
            calls.append(('models',))
            return state['models']
        def resident_memory_gib(self):
            return 0
        def ensure_local_model(self):
            calls.append(('ensure',))
        def chat_stream(self, messages, *, on_token, cancel_event, num_predict):
            calls.append(('stream', messages, num_predict))
            if on_token:
                on_token('Visible ')
            if state['cancel_during']:
                raise GenerationCancelled('cancelled')
            if state.get('failure') is not None:
                raise state['failure']
            if on_token:
                on_token('object.')
            return 'Visible object.', 12
        def _request(self, endpoint, data):
            calls.append(('unload', endpoint, data))
            if state.get('unload_failure'):
                raise LocalModelError('Cleanup unavailable')
    monkeypatch.setattr(vision, 'OllamaClient', Client)
    return calls, state, profile, hardware


@pytest.mark.parametrize('content', ['', 'data:image/png;base64,' + ENCODED, ENCODED + '\n',
                                     '!!!!', 'é', base64.b64encode(b'not image').decode(), ENCODED + '='])
def test_bad_upload_is_rejected_before_runtime(runtime, content):
    calls, *_ = runtime
    with pytest.raises(ValueError):
        vision.describe_upload(content, 'What is visible?')
    assert calls == []


def test_upload_rejects_noncanonical_padding_bits_before_runtime(runtime):
    # Both encodings decode to FF D8 FF 00, but only /9j/AA== is canonical.
    with pytest.raises(ValueError, match='base64'):
        vision.describe_upload('/9j/AB==', 'What is visible?')
    assert runtime[0] == []


def test_public_upload_validation_returns_canonical_content_without_runtime(runtime):
    assert vision.validate_upload(ENCODED) == ENCODED
    with pytest.raises(ValueError):
        vision.validate_upload('invalid')
    assert runtime[0] == []


@pytest.mark.parametrize('content', ['A' * (4 * ((8 * 1024 * 1024 + 2) // 3) + 1),
                                     base64.b64encode(b'\xff\xd8\xff' + b'x' * (8 * 1024 * 1024 - 2)).decode()],
                         ids=['encoded-too-large', 'decoded-too-large'])
def test_upload_is_bounded_before_runtime(runtime, content):
    with pytest.raises(ValueError, match='8 MiB'):
        vision.describe_upload(content, 'What is visible?')
    assert runtime[0] == []


@pytest.mark.parametrize('prompt', ['', ' ', None, 'x' * 4001, 'x\0y'],
                         ids=['empty', 'whitespace', 'nontext', 'oversized', 'nul'])
def test_invalid_prompt_does_not_contact_runtime(runtime, prompt):
    with pytest.raises(ValueError, match='question'):
        vision.describe_upload(ENCODED, prompt)
    assert runtime[0] == []


def test_success_streams_visible_answer_without_writing_image(runtime, monkeypatch):
    tokens, stages = [], []
    def reject_write(*args, **kwargs):
        pytest.fail('Uploaded images must not be persisted.')
    monkeypatch.setattr(Path, 'write_bytes', reject_write)
    monkeypatch.setattr(Path, 'write_text', reject_write)
    result = vision.describe_upload(ENCODED, 'What is visible?', on_token=tokens.append,
                                    on_stage=lambda text, **kwargs: stages.append(text))
    assert result == {'model': 'qwen3-vl:2b', 'reply': 'Visible object.', 'local': True,
                      'image_saved': False, 'metrics': {'eval_count': 12, 'seconds': .1}}
    assert ''.join(tokens) == result['reply'] and stages
    streamed = next(row for row in runtime[0] if row[0] == 'stream')
    assert streamed[1][1]['images'] == [ENCODED]
    assert 'untrusted' in streamed[1][0]['content']


def test_cloud_and_text_only_models_are_not_used(runtime):
    runtime[1]['models'] = [{'name': 'qwen3-vl:2b', 'remote_host': 'cloud'}, {'name': 'plain:1b'}]
    with pytest.raises(LocalModelError, match='vision model'):
        vision.describe_upload(ENCODED, 'What is visible?')
    assert not any(row[0] == 'stream' for row in runtime[0])
    assert next(row for row in runtime[0] if row[0] == 'choose')[1] == {'plain:1b'}


def test_already_cancelled_upload_never_contacts_runtime(runtime):
    with pytest.raises(GenerationCancelled):
        vision.describe_upload(ENCODED, 'What is visible?', cancel_event=SimpleNamespace(is_set=lambda: True))
    assert runtime[0] == []


def test_stream_cancellation_does_not_return_partial_answer(runtime):
    runtime[1]['cancel_during'] = True
    with pytest.raises(GenerationCancelled):
        vision.describe_upload(ENCODED, 'What is visible?')
    assert any(row[0] == 'unload' for row in runtime[0])


@pytest.mark.parametrize('message', ['Local generation exceeded its time budget.',
                                     'Local generation was interrupted: timed out'])
def test_vision_timeout_unloads_attempted_model_without_saving_partial(runtime, monkeypatch, message):
    runtime[1]['failure'] = LocalModelError(message)
    tokens = []
    def reject_write(*args, **kwargs):
        pytest.fail('Failed image inference must not persist image or partial answer.')
    monkeypatch.setattr(Path, 'write_bytes', reject_write)
    monkeypatch.setattr(Path, 'write_text', reject_write)
    with pytest.raises(LocalModelError, match='[Tt]ry a smaller image'):
        vision.describe_upload(ENCODED, 'What is visible?', on_token=tokens.append)
    assert tokens == ['Visible ']
    assert [row for row in runtime[0] if row[0] == 'unload'] == [
        ('unload', '/api/generate', {'model': 'qwen3-vl:2b', 'keep_alive': 0})]


@pytest.mark.parametrize('memory_pressure', [False, True])
def test_other_vision_errors_unload_and_preserve_original_exception(runtime, memory_pressure):
    error = compact_chat.ModelMemoryPressure('Memory pressure') if memory_pressure else LocalModelError('Invalid image response')
    runtime[1]['failure'] = error
    runtime[1]['unload_failure'] = True
    with pytest.raises(type(error)) as caught:
        vision.describe_upload(ENCODED, 'What is visible?')
    assert caught.value is error
    assert len([row for row in runtime[0] if row[0] == 'unload']) == 1


def test_low_memory_cancels_before_image_generation(runtime):
    runtime[3].available_ram_gib = .2
    with pytest.raises(compact_chat.ModelMemoryPressure):
        vision.describe_upload(ENCODED, 'What is visible?')
    assert not any(row[0] == 'stream' for row in runtime[0])


def test_actual_output_allowance_is_reserved_before_inference(runtime, monkeypatch):
    monkeypatch.setattr(vision, 'output_token_budget', lambda model, requested: 4000)
    with pytest.raises(ValueError, match='context'):
        vision.describe_upload(ENCODED, 'What is visible?')
    assert not any(row[0] == 'stream' for row in runtime[0])


def test_file_that_grows_after_stat_is_read_with_a_byte_limit(tmp_path, monkeypatch):
    path = tmp_path / 'image.png'
    path.write_bytes(PNG)
    original = Path.open
    reads = []
    class GrowingImage(io.BytesIO):
        def read(self, count=-1):
            reads.append(count)
            return super().read(count)
    def open_file(candidate, *args, **kwargs):
        if candidate == path:
            return GrowingImage(PNG + b'x' * (8 * 1024 * 1024))
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(ValueError, match='8 MiB'):
        vision.image_content(path)
    assert reads == [8 * 1024 * 1024 + 1]


def test_cli_file_adapter_uses_same_upload_path(tmp_path, monkeypatch):
    path = tmp_path / 'photo.jpg'; path.write_bytes(b'\xff\xd8\xffx')
    calls = []
    monkeypatch.setattr(vision, 'describe_upload', lambda *args, **kwargs: calls.append((args, kwargs)) or {'reply': 'ok'})
    assert vision.describe_image(path, 'Describe', 'http://localhost:11434') == {'reply': 'ok'}
    assert calls == [((base64.b64encode(path.read_bytes()).decode(), 'Describe'), {'base_url': 'http://localhost:11434'})]
