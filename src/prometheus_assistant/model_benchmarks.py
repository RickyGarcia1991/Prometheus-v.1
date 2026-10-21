"""Small repeatable checks of actual local model output, run on the UI worker.

These checks measure named tasks, formatting and latency. They do not certify
general intelligence, professional advice or the safety of generated programs.
"""
from dataclasses import asdict, dataclass, replace
import ast
import json
import math
import re
import time

from .hardware import detect_hardware
from .model_routing import ROLE_MODELS, choose_helper
from .ollama import GenerationCancelled, LocalModelError, OllamaClient

TEXT_ROLES = ('general', 'coding', 'translation', 'reasoning', 'writing')
MIN_FREE_GIB = 0.5
MAX_MODELS = 2
OUTPUT_TOKENS = 128
SCOPE = ('Two small deterministic checks per text role. Passing demonstrates those checks only; '
         'writing checks cover stated constraints, and coding checks parse source without executing it. '
         'Vision requires an explicit image fixture and is not tested here.')


def _word(answer):
    return answer.strip().strip('`\"\' .!?:;').casefold()


def _exact_json(answer):
    def unique_object(pairs):
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError('Duplicate JSON keys.')
        return dict(pairs)
    try:
        value = json.loads(answer, object_pairs_hook=unique_object)
    except ValueError:
        return False
    return (type(value) is dict and set(value) == {'ready', 'count'}
            and value['ready'] is True and type(value['count']) is int and value['count'] == 3)


def _addition_source(answer):
    """Inspect a narrowly specified function's AST; never execute model output."""
    try:
        tree = ast.parse(answer.strip())
    except (SyntaxError, ValueError, RecursionError):
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return False
    function = tree.body[0]
    arguments = function.args
    if (function.name != 'add' or function.decorator_list or function.returns or getattr(function, 'type_params', ())
            or arguments.posonlyargs or arguments.kwonlyargs or arguments.defaults
            or arguments.kw_defaults or arguments.vararg or arguments.kwarg
            or [arg.arg for arg in arguments.args] != ['a', 'b']
            or any(arg.annotation for arg in arguments.args)
            or len(function.body) != 1 or not isinstance(function.body[0], ast.Return)):
        return False
    expression = function.body[0].value
    return (isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add)
            and isinstance(expression.left, ast.Name) and isinstance(expression.right, ast.Name)
            and {expression.left.id, expression.right.id} == {'a', 'b'})


def _reminder(answer):
    words = re.findall(r"[\w']+", answer.casefold())
    return (bool(words) and words[0] == 'please' and len(words) <= 18 and 'monday' in words
            and re.search(r'\b9\s*(?:a\.?m\.?)\b', answer, re.I) is not None
            and not {'not', 'cancelled', 'canceled', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'} & set(words))


def _writing_line(answer):
    words = re.findall(r"[\w']+", answer.casefold())
    return (6 <= len(words) <= 12 and {'river', 'moon'} <= set(words)
            and '\n' not in answer.strip() and re.search(r'\d', answer) is None)


@dataclass(frozen=True)
class BenchmarkCase:
    role: str
    case_id: str
    prompt: str
    check_scope: str
    check: object

    def evaluate(self, answer):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
            return False
        try:
            return bool(self.check(answer))
        except (ValueError, TypeError, RecursionError):
            return False


CASES = (
    BenchmarkCase('general', 'v1.red_planet', 'Which planet is known as the Red Planet? Reply with its name only.',
                  'Exact factual name: Mars.', lambda value: _word(value) == 'mars'),
    BenchmarkCase('general', 'v1.exact_json', 'Return exactly this JSON object, without markdown: {"ready":true,"count":3}',
                  'Valid JSON with exactly the requested keys, boolean and integer.', _exact_json),
    BenchmarkCase('coding', 'v1.add_source', 'Write only a Python function named add with arguments a and b that returns their sum. No markdown.',
                  'Parsed function AST contains only a return of a+b or b+a; no code execution.', _addition_source),
    BenchmarkCase('coding', 'v1.list_length', 'In Python, which built-in function returns the length of a list? Reply with its name only.',
                  'Exact Python built-in name: len.', lambda value: _word(value) in {'len', 'len()'}),
    BenchmarkCase('translation', 'v1.french_hello', 'Translate hello into French using the standard polite greeting. Reply with one word only.',
                  'Exact standard French greeting: bonjour.', lambda value: _word(value) == 'bonjour'),
    BenchmarkCase('translation', 'v1.spanish_thanks', 'Translate thank you into Spanish. Reply with the translation only.',
                  'Exact Spanish translation: gracias.', lambda value: _word(value) == 'gracias'),
    BenchmarkCase('reasoning', 'v1.syllogism', 'All sparrows are birds. All birds are animals. Are all sparrows animals? Reply yes or no only.',
                  'Transitive classification entails yes.', lambda value: _word(value) == 'yes'),
    BenchmarkCase('reasoning', 'v1.two_colors', 'A box holds at least three marbles, each red or blue. Without looking, how many must you draw to guarantee two of the same color? Reply with one integer.',
                  'Two color classes require three draws to guarantee a repeated color.', lambda value: _word(value) == '3'),
    BenchmarkCase('writing', 'v1.polite_reminder', 'Write a polite reminder that the meeting is Monday at 9 AM. Start with Please and use at most 18 words.',
                  'Preserves Monday and 9 AM, starts with Please, at most 18 words; not a prose quality score.', _reminder),
    BenchmarkCase('writing', 'v1.constrained_line', 'Write exactly one line of 6 to 12 words, including river and moon. Do not include any digits.',
                  'One line, 6–12 words, both requested words and no digits; not a literary quality score.', _writing_line),
)


class _RunCancellation:
    def __init__(self, requested):
        self.requested = requested
        self.memory_reason = None
        self.minimum_free = None
        self.next_check = 0

    def observe(self, free):
        if free is None:
            self.memory_reason = 'available_memory_unknown'
        else:
            self.minimum_free = free if self.minimum_free is None else min(self.minimum_free, free)
            if free < MIN_FREE_GIB:
                self.memory_reason = 'memory_pressure'

    def is_set(self):
        if self.requested is not None and self.requested.is_set():
            return True
        now = time.monotonic()
        if now >= self.next_check:
            self.next_check = now + .5
            self.observe(detect_hardware().available_ram_gib)
        return self.memory_reason is not None


def _profile(name, hardware, resident):
    role = 'coding' if name.startswith('qwen2.5-coder:') else 'general'
    return choose_helper({name}, hardware, role, resident)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


class _BenchmarkModel:
    """Release only the helper for which this benchmark attempted inference."""
    def __init__(self, model, base_url, report):
        self.model = model
        self.base_url = base_url
        self.report = report
        self.touched = False

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if not self.touched:
            return False
        started = time.monotonic()
        record = {'model': self.model, 'timeout_seconds': 2}
        try:
            client = OllamaClient(self.base_url, self.model, timeout=2)
            response = client._request('/api/generate', {'model': self.model, 'keep_alive': 0})
            if not isinstance(response, dict) or response.get('done') is not True:
                raise LocalModelError('Local runtime did not confirm model unload.')
            record['state'] = 'unloaded'
        except (LocalModelError, OSError, ValueError) as unload_error:
            record.update(state='unconfirmed', error=str(unload_error)[:500],
                          note='The benchmark answer scores remain valid; model memory release was not confirmed.')
            # Cancelled jobs return an exception instead of the detailed report.
            # Preserve cancellation while making a cleanup problem visible there.
            if isinstance(error, GenerationCancelled):
                error.args = (str(error)+' Model unload could not be confirmed; it may remain resident briefly.',)
        record['seconds'] = time.monotonic()-started
        self.report['model_cleanup'].append(record)
        # A return inside the context evaluates finish() before __exit__. Keep its
        # final snapshot accurate after the bounded unload completes.
        if 'hardware_after' in self.report:
            self.report['hardware_after'] = asdict(detect_hardware())
        return False


def run_benchmarks(evidence, installed, hardware=None, *, base_url='http://127.0.0.1:11434',
                   cancel_event=None, on_stage=None, on_token=None):
    """Measure at most two fitting helpers; never download models or execute answers.

    ``hardware`` is an optional initial snapshot. Live memory is still checked
    between cases and during streaming. The caller must serialize this with chat,
    embeddings and other inference on its worker. Cancellation propagates without
    adding an inaccurate failure or negative benchmark result.
    """
    if not isinstance(installed, (set, frozenset, list, tuple)) or any(not isinstance(name, str) for name in installed):
        raise ValueError('Benchmarks require an installed model-name collection.')
    installed = set(installed)
    hardware = hardware or detect_hardware()
    report = {'state': 'preflight', 'scope': SCOPE, 'hardware_before': asdict(hardware),
              'minimum_free_gib': MIN_FREE_GIB, 'max_models': MAX_MODELS, 'output_token_limit': OUTPUT_TOKENS,
              'models': [], 'cases': [], 'skipped_models': [], 'model_cleanup': [],
              'skipped_roles': [{'role': 'vision', 'reason': 'An explicit image and independently checked expected answer are required.'}]}
    guard = _RunCancellation(cancel_event)

    def stopped():
        if guard.is_set():
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled('Model checks cancelled; unfinished checks were not scored.')
            report['state'] = guard.memory_reason
            return True
        return False

    def finish(state=None):
        if state:
            report['state'] = state
        report['minimum_observed_free_gib'] = guard.minimum_free
        report['hardware_after'] = asdict(detect_hardware())
        report['passed'] = sum(row.get('passed') is True for row in report['cases'])
        report['checked'] = sum(type(row.get('passed')) is bool for row in report['cases'])
        report['evidence'] = evidence.summary()
        return report

    if stopped():
        return finish()
    if hardware.available_ram_gib is None or hardware.available_ram_gib < MIN_FREE_GIB:
        return finish('insufficient_memory')
    control = OllamaClient(base_url=base_url, timeout=2)
    resident = control.resident_memory_gib()
    known = set().union(*(set(ROLE_MODELS[role]) for role in TEXT_ROLES))
    profiles = []
    for name in sorted(installed):
        if name not in known:
            report['skipped_models'].append({'model': name, 'reason': 'No configured text helper profile.'})
            continue
        if name.split(':', 1)[0] == 'gpt-oss':
            report['skipped_models'].append({'model': name, 'reason': 'This reasoning client requires an output budget above 128 tokens.'})
            continue
        profile = _profile(name, hardware, resident)
        if profile is None:
            report['skipped_models'].append({'model': name, 'reason': 'Does not fit the current memory budget.'})
        else:
            profiles.append(profile)
    measured = {(row['model'], row['role']): row['cases'] for row in evidence.summary()['benchmarks']}
    def priority(profile):
        missing = sum(max(0, 2-measured.get((profile.model, role), 0)) for role in TEXT_ROLES)
        return (-missing, profile.min_ram_gib, profile.context, profile.model)
    profiles.sort(key=priority)
    for profile in profiles[MAX_MODELS:]:
        report['skipped_models'].append({'model': profile.model, 'reason': 'Run limit: at most two models; run checks again to measure other helpers.'})
    selected = profiles[:MAX_MODELS]
    if not selected:
        return finish('no_fitting_models')
    had_errors = had_deferrals = False
    for selected_profile in selected:
        if stopped():
            return finish()
        if on_stage:
            on_stage('Checking installed helper '+selected_profile.model, reset=True)
        # Tags/health requests remain bounded independently of inference timeout.
        local = OllamaClient(base_url, selected_profile.model, timeout=3)
        try:
            local.ensure_local_model()
        except LocalModelError as error:
            if stopped():
                return finish()
            report['skipped_models'].append({'model': selected_profile.model, 'reason': str(error)[:500]})
            had_errors = True
            continue
        report['models'].append(selected_profile.model)
        with _BenchmarkModel(selected_profile.model, base_url, report) as owned:
            for case in CASES:
                if stopped():
                    return finish()
                current = detect_hardware()
                guard.observe(current.available_ram_gib)
                if current.available_ram_gib is None or current.available_ram_gib < MIN_FREE_GIB:
                    return finish('memory_pressure')
                observed_resident = control.resident_memory_gib()
                fitting = _profile(selected_profile.model, current, observed_resident)
                if fitting is None:
                    report['skipped_models'].append({'model': selected_profile.model, 'reason': 'Memory changed; remaining checks deferred.'})
                    had_deferrals = True
                    break
                # A bounded 1024-token context is sufficient for each short standalone fixture.
                fitting = replace(fitting, context=min(fitting.context, 1024))
                client = OllamaClient(base_url, fitting.model, timeout=45, num_ctx=fitting.context, num_thread=fitting.cpu_threads)
                if on_stage:
                    on_stage(f'Checking {fitting.model} · {case.role} · {case.case_id}', reset=True)
                if stopped():
                    return finish()
                before = time.monotonic()
                row = {'model': fitting.model, 'role': case.role, 'case_id': case.case_id, 'prompt': case.prompt,
                       'check_scope': case.check_scope, 'context': fitting.context,
                       'free_before_gib': current.available_ram_gib, 'resident_before_gib': observed_resident}
                try:
                    owned.touched = True
                    answer, _ = client.chat_stream([{'role': 'user', 'content': case.prompt}],
                                                  on_token=on_token, cancel_event=guard, num_predict=OUTPUT_TOKENS)
                    if stopped():
                        row.update(state='not_scored_memory_pressure', passed=None, answer=answer[:8000])
                        report['cases'].append(row)
                        return finish()
                    metrics = client.last_metrics
                    seconds = _number(metrics.get('seconds'))
                    if seconds is None:
                        seconds = time.monotonic()-before
                    first = _number(metrics.get('first_token_seconds'))
                    tokens = _number(metrics.get('eval_count'))
                    resident_after = control.resident_memory_gib()
                    free_after = detect_hardware().available_ram_gib
                    guard.observe(free_after)
                    passed = case.evaluate(answer)
                    row.update(state='checked', passed=passed, answer=answer[:8000], seconds=seconds,
                               first_token_seconds=first, resident_gib=resident_after,
                               free_after_gib=free_after,
                               eval_count=tokens, end_to_end_tokens_per_second=(tokens/seconds if tokens is not None and seconds else None),
                               load_seconds=_number(metrics.get('load_seconds')))
                    evidence.record_benchmark(fitting.model, case.role, case.case_id, passed=passed, seconds=seconds,
                                              first_token=first, resident_gib=resident_after, context=fitting.context)
                    evidence.record_success(fitting.model)
                    report['cases'].append(row)
                except GenerationCancelled:
                    if cancel_event is not None and cancel_event.is_set():
                        raise
                    if guard.memory_reason:
                        row.update(state='not_scored_memory_pressure', passed=None, seconds=time.monotonic()-before)
                        report['cases'].append(row)
                        return finish(guard.memory_reason)
                    raise
                except LocalModelError as error:
                    if stopped():
                        return finish()
                    seconds = time.monotonic()-before
                    row.update(state='generation_failed', passed=False, seconds=seconds, error=str(error)[:500],
                               free_after_gib=detect_hardware().available_ram_gib)
                    evidence.record_benchmark(fitting.model, case.role, case.case_id, passed=False, seconds=seconds,
                                              context=fitting.context)
                    evidence.record_failure(fitting.model, error)
                    report['cases'].append(row)
                    had_errors = True
                    break
    return finish('completed_with_errors' if had_errors else 'completed_with_deferrals' if had_deferrals else 'complete')
