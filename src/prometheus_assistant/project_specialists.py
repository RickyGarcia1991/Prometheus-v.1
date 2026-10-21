"""Two sequential local advisory roles over one exact proposed Code snapshot.

The primary app still owns task execution and owner authorization. This module
adds no writes, model installation, remote dispatch or background retry. A local
slot also prevents overlapping specialist coordinators from invoking models.
Receipts detect corruption; they never authenticate browser-provided records.
"""
from copy import deepcopy
import hashlib
import json
import threading

from . import advisory_review_runtime as runtime, project_advisory, specialist_resources
from .advisory_review import _hash, _json_bytes, _keys, _path, prepare_review, validate_saved_advice
from .ollama import GenerationCancelled


PIPELINE_VERSION = 'project-specialists-v1'
MAX_TEAM_BYTES = 110_000
_CANONICAL_LIMIT = 1_000_000
_ROLES = (('correctness', 'Correctness review'), ('test_coverage', 'Test coverage review'))
_KEYS = {'schema_version', 'pipeline_version', 'candidate_sha256', 'files', 'status',
         'max_model_concurrency', 'authorized', 'approval_required', 'stages', 'receipt_sha256'}
_STAGE_KEYS = {'role', 'label', 'status', 'advice', 'resources'}
_MODEL_SLOT = threading.Lock()
# Historical display accepts only these known prior role versions. Exact
# retention for applying a candidate still requires the current prompt below.
_PRIOR_PROMPT_VERSION = {'correctness': 'local-advisory-v3',
                         'test_coverage': 'local-advisory-test-coverage-v1'}


def _digest(value):
    return hashlib.sha256(_json_bytes(value, _CANONICAL_LIMIT)).hexdigest()


def _bounded(record):
    _json_bytes(record, _CANONICAL_LIMIT)
    if len(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')) > MAX_TEAM_BYTES:
        raise ValueError('Specialist team exceeds its retained evidence limit.')


def _binding(candidate, files):
    return prepare_review(candidate, files, author=None,
        reviewer={'provider': 'ollama-local', 'model': 'not-selected', 'artifact_sha256': None},
        prompt_version=PIPELINE_VERSION, prompt_sha256='0' * 64)


def _progress(app, message):
    callback = getattr(app, 'progress', None)
    if callable(callback):
        try:
            callback(message)
        except Exception:
            pass  # Progress displays cannot alter source or owner validation.


def validate_saved_team(record):
    """Check stored structure/integrity; retain additionally checks exact bytes."""
    _keys(record, _KEYS, 'specialist team')
    _bounded(record)
    if (type(record['schema_version']) is not int or record['schema_version'] != 1
            or record['pipeline_version'] != PIPELINE_VERSION
            or type(record['max_model_concurrency']) is not int or record['max_model_concurrency'] != 1
            or record['authorized'] is not False or record['approval_required'] is not True
            or type(record['status']) is not str or record['status'] not in {'completed', 'incomplete', 'cancelled'}):
        raise ValueError('Invalid specialist team scope.')
    if record['receipt_sha256'] != _digest({k: v for k, v in record.items() if k != 'receipt_sha256'}):
        raise ValueError('Retained specialist receipt changed.')
    _hash(record['candidate_sha256'], 'specialist candidate SHA256')
    files = record['files']
    if type(files) is not list or not 1 <= len(files) <= 8:
        raise ValueError('Invalid specialist file list.')
    aliases, total = set(), 0
    for row in files:
        _keys(row, {'path', 'sha256', 'bytes', 'lines'}, 'specialist file')
        path = _path(row['path'])
        _hash(row['sha256'], 'specialist file SHA256')
        if (path.casefold() in aliases or type(row['bytes']) is not int or type(row['lines']) is not int
                or not 0 <= row['lines'] <= row['bytes'] <= 64000):
            raise ValueError('Invalid specialist file bounds.')
        aliases.add(path.casefold())
        total += row['bytes']
    if total > 64000 or [row['path'] for row in files] != sorted(row['path'] for row in files):
        raise ValueError('Invalid specialist snapshot bounds or order.')
    stages = record['stages']
    if type(stages) is not list or len(stages) != len(_ROLES):
        raise ValueError('Both specialist stages must be retained.')
    stopped, expected_status, authors = False, 'completed', []
    for stage, (role, label) in zip(stages, _ROLES):
        _keys(stage, _STAGE_KEYS, 'specialist stage')
        if stage['role'] != role or stage['label'] != label:
            raise ValueError('Specialist role order or label changed.')
        status, advice, resources = stage['status'], stage['advice'], stage['resources']
        if type(status) is not str or status not in {'completed', 'deferred', 'unavailable', 'cancelled', 'not_run'}:
            raise ValueError('Invalid specialist stage status.')
        if stopped:
            if status != 'not_run' or advice is not None or resources is not None:
                raise ValueError('No specialist may run after an incomplete stage.')
            continue
        if status == 'not_run':
            raise ValueError('Missing initial specialist stage outcome.')
        if resources is not None:
            specialist_resources.validate(resources)
        if advice is not None:
            try:
                validate_saved_advice(advice)
            except (TypeError, KeyError) as error:
                raise ValueError('Invalid retained specialist advice.') from error
        if status == 'completed':
            if (advice is None or advice['status'] != 'qualified_advisory'
                    or resources is None or resources['decision'] != 'run_local'):
                raise ValueError('Completed specialist lacks admitted qualified advice.')
            context = advice['report']['context']
            if (type(advice['report']['schema_version']) is not int or type(context['schema_version']) is not int
                    or context['candidate_sha256'] != record['candidate_sha256'] or context['files'] != record['files']
                    or context['prompt_version'] not in (runtime.prompt_version(role), _PRIOR_PROMPT_VERSION[role])
                    or context['reviewer']['provider'] != 'ollama-local'
                    or context['reviewer']['artifact_sha256'] is None):
                raise ValueError('Specialist report does not match its role and snapshot.')
            authors.append(context['author'])
        elif status == 'deferred':
            if advice is not None or resources is None or resources['decision'] != 'defer':
                raise ValueError('Deferred specialist lacks its resource decision.')
        elif status == 'unavailable':
            if (advice is None or advice['status'] not in {'unavailable', 'timed_out', 'invalid_input', 'invalid_response'}
                    or resources is None or resources['decision'] != 'run_local'):
                raise ValueError('Unavailable specialist has an inconsistent outcome.')
        elif (advice is not None and advice['status'] != 'cancelled'
              or resources is not None and resources['decision'] != 'run_local'):
            raise ValueError('Cancelled specialist has an inconsistent outcome.')
        if status != 'completed':
            stopped = True
            expected_status = 'cancelled' if status == 'cancelled' else 'incomplete'
    if record['status'] != expected_status or any(author != authors[0] for author in authors[1:]):
        raise ValueError('Specialist team status or author changed.')
    return deepcopy(record)


def retain(candidate, files, record):
    """Validate saved exact-byte evidence only; never run models or measurements."""
    if record is None:
        return None
    record = validate_saved_team(record)
    binding = _binding(candidate, files)
    if record['candidate_sha256'] != binding['candidate_sha256'] or record['files'] != binding['files']:
        raise ValueError('Retained specialist team belongs to a different candidate.')
    for stage in record['stages']:
        advice = stage['advice']
        if advice is None:
            continue
        project_advisory.retain(candidate, files, advice)
        if stage['status'] == 'completed':
            identity = runtime.prompt_identity(candidate, files, role=stage['role'])
            context = advice['report']['context']
            if any(context[key] != value for key, value in identity.items()):
                raise ValueError('Retained specialist prompt does not match its role.')
    return record


def prepare(app, engine, candidate, *, verify):
    """Prepare optional advisory stages; caller rechecks owner before applying.

    verify() must raise on owner/source changes. It runs before and after each
    stage, including between roles. Incomplete work is retained without retry.
    """
    if not callable(verify):
        raise ValueError('Specialist review requires an owner/source verifier.')
    if verify() is False:
        raise ValueError('Specialist owner/source verification failed.')
    files = project_advisory.proposed_files(engine, candidate)
    binding = _binding(candidate, files)
    frozen = deepcopy(candidate)
    author = deepcopy(getattr(engine, 'author_identity', None))
    record = dict(schema_version=1, pipeline_version=PIPELINE_VERSION,
        candidate_sha256=binding['candidate_sha256'], files=binding['files'], status='completed',
        max_model_concurrency=1, authorized=False, approval_required=True,
        stages=[dict(role=role, label=label, status='not_run', advice=None, resources=None)
                for role, label in _ROLES])

    def unchanged():
        if verify() is False:
            raise ValueError('Specialist owner/source verification failed.')
        fresh_files = project_advisory.proposed_files(engine, candidate)
        if fresh_files != files or _binding(candidate, fresh_files)['candidate_sha256'] != binding['candidate_sha256']:
            raise ValueError('Project changed during specialist review.')

    for stage in record['stages']:
        unchanged()
        if app.cancel_event.is_set():
            stage['status'], record['status'] = 'cancelled', 'cancelled'
            break
        _progress(app, stage['label'] + ': checking current RAM, CPU and power')
        acquired = _MODEL_SLOT.acquire(blocking=False)
        try:
            stage['resources'] = specialist_resources.admit(app, slot_available=acquired)
            unchanged()
            if app.cancel_event.is_set():
                stage['resources'] = None
                stage['status'], record['status'] = 'cancelled', 'cancelled'
            elif stage['resources']['decision'] == 'defer':
                stage['status'], record['status'] = 'deferred', 'incomplete'
                _progress(app, stage['label'] + ': ' + ' '.join(stage['resources']['reasons']))
            else:
                _progress(app, stage['label'] + ': admitted; selecting a fitting local model and context')
                try:
                    advice = runtime.run_review(app, deepcopy(frozen), dict(files), author=deepcopy(author), role=stage['role'])
                except GenerationCancelled:
                    advice = runtime._result('cancelled', 'cancelled')
                unchanged()
                if app.cancel_event.is_set():
                    advice = runtime._result('cancelled', 'cancelled', attempted=bool(advice.get('attempted')))
                stage['advice'] = project_advisory.retain(frozen, files, advice)
                if advice['status'] == 'qualified_advisory':
                    identity = runtime.prompt_identity(frozen, files, role=stage['role'])
                    if any(advice['report']['context'][key] != value for key, value in identity.items()):
                        raise ValueError('Specialist prompt does not match its role.')
                    stage['status'] = 'completed'
                    try:
                        # Account for the receipt before accepting evidence into
                        # bounded history; never truncate a qualified report.
                        _bounded(dict(record, receipt_sha256='0' * 64))
                    except ValueError:
                        stage['advice'] = dict(status='unavailable', reason_code='evidence_limit',
                            reason='Complete specialist evidence exceeds the retained review limit; exact owner review is required.',
                            attempted=True, authorized=False, report=None)
                        stage['status'], record['status'] = 'unavailable', 'incomplete'
                        _progress(app, stage['label'] + ': ' + stage['advice']['reason'])
                else:
                    stage['status'] = 'cancelled' if advice['status'] == 'cancelled' else 'unavailable'
                    record['status'] = 'cancelled' if stage['status'] == 'cancelled' else 'incomplete'
                    _progress(app, stage['label'] + ': ' + advice['reason'])
        finally:
            if acquired:
                _MODEL_SLOT.release()
        if stage['status'] != 'completed':
            break
    unchanged()
    record['receipt_sha256'] = _digest(record)
    return retain(frozen, files, record)


def findings(record):
    """Keep every role's qualified claim, including conflicting claims."""
    if record is None:
        return []
    record = validate_saved_team(record)
    rows = []
    for stage in record['stages']:
        if stage['status'] == 'completed':
            rows.extend(dict(row, specialist_role=stage['role'], specialist_label=stage['label'])
                        for row in stage['advice']['report']['findings'])
    return rows


def needs_exact_review(record):
    if record is None:
        return False
    record = validate_saved_team(record)
    return record['status'] != 'completed' or any(row['risk'] in {'high', 'critical'} for row in findings(record))
