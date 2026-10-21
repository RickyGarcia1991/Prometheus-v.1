"""Benchmark workflow integration with real SQLite evidence and fixture inference."""
import threading

import pytest

from prometheus_assistant import model_benchmarks as module
from prometheus_assistant.hardware import HardwareProfile
from prometheus_assistant.model_quality import ModelEvidence
from prometheus_assistant.ollama import GenerationCancelled, LocalModelError

ANSWERS = {
    'v1.red_planet': 'Mars',
    'v1.exact_json': '{"count":3,"ready":true}',
    'v1.add_source': 'def add(a, b):\n    return a + b',
    'v1.list_length': 'len',
    'v1.french_hello': 'Bonjour',
    'v1.spanish_thanks': 'Gracias',
    'v1.syllogism': 'Yes',
    'v1.two_colors': '3',
    'v1.polite_reminder': 'Please remember our meeting is Monday at 9 AM.',
    'v1.constrained_line': 'The quiet river reflects the bright moon tonight.',
}


@pytest.mark.parametrize('case', module.CASES, ids=lambda case: case.case_id)
def test_checks_accept_independent_expected_answers_and_reject_unrelated_text(case):
    assert case.evaluate(ANSWERS[case.case_id])
    assert not case.evaluate('unrelated incorrect response')
    assert not case.evaluate('')
    assert not case.evaluate(None)


def test_coding_checks_do_not_accept_side_effects_or_execute_generated_code(tmp_path):
    check = next(case for case in module.CASES if case.case_id == 'v1.add_source')
    target = tmp_path / 'must-not-exist'
    assert not check.evaluate(f'open({str(target)!r}, "w").write("executed")\ndef add(a, b): return a+b')
    assert not target.exists()
    assert not check.evaluate('def add(a, b): return a-b')
    assert not check.evaluate('def add(a, b): return a+a')
    assert not check.evaluate('@dangerous\ndef add(a, b): return a+b')
    assert not check.evaluate('def add(a, b=dangerous()): return a+b')
    assert not check.evaluate('def add[T: dangerous()](a, b): return a+b')
    assert check.evaluate('def add(a, b): return b+a')


def test_exact_json_rejects_duplicate_keys_even_if_last_value_is_expected():
    check = next(case for case in module.CASES if case.case_id == 'v1.exact_json')
    assert not check.evaluate('{"ready": false, "ready": true, "count": 3}')
    assert not check.evaluate('{"ready": true, "count": true}')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    hardware = HardwareProfile(32, 8, 'Windows', 16)
    state = {'hardware': hardware, 'calls': [], 'checks': [], 'failure': None, 'wrong': False,
             'unloads': [], 'cleanup_failure': False,
             'cancel': None, 'drop_memory': False}
    monkeypatch.setattr(module, 'detect_hardware', lambda: state['hardware'])
    by_prompt = {case.prompt: case for case in module.CASES}

    class FixtureClient:
        def __init__(self, base_url='http://127.0.0.1:11434', model='default', timeout=2, num_ctx=2048, num_thread=None):
            self.model, self.num_ctx, self.timeout = model, num_ctx, timeout
            self.last_metrics = {}

        def resident_memory_gib(self):
            return .6

        def ensure_local_model(self):
            state['checks'].append(self.model)
            return {'name': self.model, 'digest': 'a'*64}

        def _request(self, path, payload):
            state['unloads'].append({'path': path, 'payload': payload, 'timeout': self.timeout})
            if state['cleanup_failure']:
                raise LocalModelError('Unload acknowledgement unavailable')
            return {'done': True, 'done_reason': 'unload'}

        def chat_stream(self, messages, *, on_token, cancel_event, num_predict):
            state['calls'].append({'model': self.model, 'context': self.num_ctx, 'messages': messages, 'num_predict': num_predict})
            if state['cancel'] is not None:
                state['cancel'].set()
            if state['drop_memory']:
                state['hardware'] = HardwareProfile(32, 8, 'Windows', .4)
                # Force the next guard poll without sleeping.
                cancel_event.next_check = 0
            if cancel_event.is_set():
                raise GenerationCancelled('Fixture request interrupted')
            if state['failure']:
                raise LocalModelError(state['failure'])
            case = by_prompt[messages[0]['content']]
            answer = 'wrong' if state['wrong'] else ANSWERS[case.case_id]
            self.last_metrics = {'seconds': .25, 'first_token_seconds': .1, 'eval_count': 8, 'load_seconds': .02}
            if on_token:
                on_token(answer)
            return answer, 8

    monkeypatch.setattr(module, 'OllamaClient', FixtureClient)
    evidence = ModelEvidence(tmp_path / 'evidence.sqlite3')
    return state, evidence


def test_actual_stream_contract_two_models_all_text_roles_and_sqlite_evidence(rig):
    state, evidence = rig
    stages, tokens = [], []
    report = module.run_benchmarks(evidence, {'qwen2.5-coder:0.5b', 'qwen3:0.6b', 'llama3.2:1b'},
        on_stage=lambda value, **kwargs: stages.append(value), on_token=tokens.append)
    assert report['state'] == 'complete' and len(report['models']) == 2
    assert report['checked'] == report['passed'] == 20
    assert len(state['calls']) == 20 and len(state['checks']) == 2
    assert len(tokens) == 20 and stages
    assert {row['role'] for row in report['cases']} == set(module.TEXT_ROLES)
    assert report['skipped_roles'][0]['role'] == 'vision'
    assert any('Run limit' in row['reason'] for row in report['skipped_models'])
    assert all(call['num_predict'] == 128 and call['context'] == 1024 for call in state['calls'])
    assert all(len(call['messages']) == 1 and call['messages'][0]['role'] == 'user' for call in state['calls'])
    assert all(row['end_to_end_tokens_per_second'] == 32 for row in report['cases'])
    assert all(row['resident_gib'] == .6 and row['free_before_gib'] == 16 for row in report['cases'])
    assert len(evidence.summary()['benchmarks']) == 10
    assert [row['payload']['model'] for row in state['unloads']] == report['models']
    assert all(row['path'] == '/api/generate' and row['payload']['keep_alive'] == 0 and row['timeout'] == 2
               for row in state['unloads'])
    assert all(row['state'] == 'unloaded' for row in report['model_cleanup'])


def test_unmeasured_models_are_prioritized_over_current_evidence(rig):
    state, evidence = rig
    for case in module.CASES:
        evidence.record_benchmark('qwen2.5-coder:0.5b', case.role, case.case_id, passed=True, seconds=.1)
    result = module.run_benchmarks(evidence, {'qwen2.5-coder:0.5b', 'qwen3:0.6b', 'llama3.2:1b'})
    assert 'qwen2.5-coder:0.5b' not in result['models']
    assert set(result['models']) == {'qwen3:0.6b', 'llama3.2:1b'}


def test_incorrect_answers_are_scored_false_without_runtime_failure_penalty(rig):
    state, evidence = rig
    state['wrong'] = True
    report = module.run_benchmarks(evidence, {'qwen3:0.6b'})
    assert report['checked'] == 10 and report['passed'] == 0
    assert all(row['state'] == 'checked' and not row['passed'] for row in report['cases'])
    assert all(row['passed'] == 0 for row in evidence.summary()['benchmarks'])
    assert not evidence.summary()['failures']


def test_generation_failure_is_recorded_and_remaining_model_cases_stop(rig):
    state, evidence = rig
    state['failure'] = 'Cannot complete inference'
    result = module.run_benchmarks(evidence, {'qwen3:0.6b'})
    assert result['state'] == 'completed_with_errors' and len(state['calls']) == 1
    assert result['cases'][0]['state'] == 'generation_failed' and result['cases'][0]['passed'] is False
    assert evidence.summary()['benchmarks'][0]['passed'] == 0
    assert evidence.summary()['failures'][0]['model'] == 'qwen3:0.6b'


def test_cancellation_does_not_penalize_model_or_score_incomplete_case(rig):
    state, evidence = rig
    cancelled = threading.Event()
    state['cancel'] = cancelled
    with pytest.raises(GenerationCancelled):
        module.run_benchmarks(evidence, {'qwen3:0.6b'}, cancel_event=cancelled)
    assert len(state['calls']) == 1
    assert not evidence.summary()['benchmarks'] and not evidence.summary()['failures']
    assert [row['payload']['model'] for row in state['unloads']] == ['qwen3:0.6b']


def test_already_cancelled_starts_no_model(rig):
    state, evidence = rig
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(GenerationCancelled):
        module.run_benchmarks(evidence, {'qwen3:0.6b'}, cancel_event=cancelled)
    assert not state['calls'] and not state['checks']
    assert not state['unloads']


def test_low_or_unknown_memory_prevents_inference(rig):
    state, evidence = rig
    for free in (.49, None):
        state['hardware'] = HardwareProfile(8, 8, 'Windows', free)
        result = module.run_benchmarks(evidence, {'qwen3:0.6b'})
        assert result['state'] in {'memory_pressure', 'available_memory_unknown'}
        assert result['checked'] == 0
    assert not state['calls'] and not state['checks']
    assert not state['unloads']


def test_memory_pressure_during_stream_stops_without_false_model_failure(rig):
    state, evidence = rig
    state['drop_memory'] = True
    result = module.run_benchmarks(evidence, {'qwen3:0.6b'})
    assert result['state'] == 'memory_pressure'
    assert result['minimum_observed_free_gib'] == .4
    assert result['checked'] == 0
    assert result['cases'][0]['passed'] is None
    assert not evidence.summary()['benchmarks'] and not evidence.summary()['failures']
    assert [row['payload']['model'] for row in state['unloads']] == ['qwen3:0.6b']
    assert result['model_cleanup'][0]['state'] == 'unloaded'


def test_unknown_embedding_vision_and_unbounded_reasoning_models_are_not_started(rig):
    state, evidence = rig
    result = module.run_benchmarks(evidence, {'invented:99b', 'qwen3-embedding:0.6b', 'qwen3-vl:2b', 'gpt-oss:20b'})
    assert result['state'] == 'no_fitting_models' and not state['calls']
    assert len(result['skipped_models']) == 4
    assert any('128' in row['reason'] for row in result['skipped_models'])


def test_memory_fit_is_rechecked_between_cases(rig):
    state, evidence = rig
    def after_token(piece):
        # Above the hard floor, but too little for the ordinary qwen profile.
        state['hardware'] = HardwareProfile(8, 8, 'Windows', .7)
    report = module.run_benchmarks(evidence, {'qwen3:0.6b'}, on_token=after_token)
    assert len(state['calls']) == 1 and report['checked'] == 1
    assert report['state'] == 'completed_with_deferrals' and report['minimum_observed_free_gib'] == .7
    assert any('Memory changed' in row['reason'] for row in report['skipped_models'])


def test_cleanup_failure_is_reported_without_falsifying_answer_scores(rig):
    state, evidence = rig
    state['cleanup_failure'] = True
    report = module.run_benchmarks(evidence, {'qwen3:0.6b'})
    assert report['state'] == 'complete' and report['passed'] == report['checked'] == 10
    assert report['model_cleanup'][0]['state'] == 'unconfirmed'
    assert 'unavailable' in report['model_cleanup'][0]['error']
    assert not evidence.summary()['failures']


def test_cancel_cleanup_failure_remains_cancellation_and_explains_unconfirmed_release(rig):
    state, evidence = rig
    state['cleanup_failure'] = True
    state['cancel'] = threading.Event()
    with pytest.raises(GenerationCancelled, match='unload could not be confirmed'):
        module.run_benchmarks(evidence, {'qwen3:0.6b'}, cancel_event=state['cancel'])
    assert len(state['unloads']) == 1
    assert not evidence.summary()['benchmarks'] and not evidence.summary()['failures']


def test_cancel_from_stage_before_first_inference_does_not_unload_resident_models(rig):
    state, evidence = rig
    cancelled = threading.Event()
    def stage(message, **kwargs):
        if 'v1.red_planet' in message:
            cancelled.set()
    with pytest.raises(GenerationCancelled):
        module.run_benchmarks(evidence, {'qwen3:0.6b'}, cancel_event=cancelled, on_stage=stage)
    assert not state['calls'] and not state['unloads']
