"""Pure, bounded syntax and fixed-profile lint protocol; never dispatches work.

Digests bind data; they do not authenticate a worker or its assertions. The
caller must supply trusted local admission observations separately and establish
transport identity. Workers must enforce root containment and reject symlinks.
"""
import hashlib
import json
import math
import re

_FIELDS = {'schema_version', 'job_id', 'device_id', 'worker_digest', 'operation',
           'timeout_seconds', 'files'}
_RECEIPT = {'schema_version', 'job_id', 'device_id', 'worker_digest',
            'request_digest', 'state', 'checks'}
LINT_OPERATION = 'python-ruff-check-v1'
LINT_PROFILE = 'ruff-python-v1'
LINT_MAX_DIAGNOSTICS = 16
LINT_MAX_MESSAGE_BYTES = 160
LINT_MAX_RECEIPT_BYTES = 8192
_LINT_FIELDS = _FIELDS | {'tool', 'profile'}
_LINT_RECEIPT = _RECEIPT | {'operation', 'tool', 'profile', 'status',
                             'diagnostics', 'reason', 'source_unchanged'}
_DIAGNOSTIC = {'path', 'code', 'message', 'line', 'column', 'end_line', 'end_column'}
_PRIVATE = {'__pycache__', 'node_modules', 'site-packages'}
_RESERVED = re.compile(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)


def _matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _path(value):
    # A deliberately portable subset avoids drive names, alternate streams,
    # traversal, hidden/private metadata and Windows case aliasing.
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or not value.endswith('.py'):
        raise ValueError('Choose a bounded relative Python path.')
    parts = value.split('/')
    if any(not _matches(part, r'[A-Za-z0-9_][A-Za-z0-9_.-]*')
           or part.endswith(('.', ' ')) or part.casefold() in _PRIVATE
           or _RESERVED.match(part) for part in parts):
        raise ValueError('Private metadata or nonportable Python path.')
    return value


def normalize_request(raw):
    """Return a detached syntax-v1 or fixed Ruff-v2 request and exact digest.

    Accept an already normalized request only when its digest still matches.
    Content and file ordering are preserved exactly (no newline normalization).
    Malformed or excessive requests raise bounded ValueError messages.
    """
    lint = isinstance(raw, dict) and type(raw.get('schema_version')) is int and raw['schema_version'] == 2
    fields = _LINT_FIELDS if lint else _FIELDS
    if not isinstance(raw, dict) or set(raw) not in (fields, fields | {'request_digest'}):
        raise ValueError('Unexpected or missing syntax request fields.')
    if (type(raw['schema_version']) is not int or raw['schema_version'] != (2 if lint else 1)
            or not _matches(raw['job_id'], r'[0-9a-f]{32}')
            or not _matches(raw['worker_digest'], r'[0-9a-f]{64}')
            or not _matches(raw['device_id'], r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
            or raw['operation'] != (LINT_OPERATION if lint else 'python-syntax-check')
            or type(raw['timeout_seconds']) is not int or not 1 <= raw['timeout_seconds'] <= 30):
        raise ValueError('Invalid syntax request identity, operation or timeout.')
    if lint and (raw['profile'] != LINT_PROFILE or not _lint_tool(raw['tool'])):
        raise ValueError('Invalid fixed lint profile or pinned tool identity.')
    files = raw['files']
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError('A syntax request requires one to eight files.')
    seen, total, normalized = set(), 0, []
    for row in files:
        if not isinstance(row, dict) or set(row) != {'path', 'content'}:
            raise ValueError('Unexpected or missing syntax file fields.')
        path = _path(row['path'])
        if path.casefold() in seen:
            raise ValueError('Duplicate or case-colliding syntax paths.')
        seen.add(path.casefold())
        content = row['content']
        if not isinstance(content, str) or len(content) > 256000:
            raise ValueError('Syntax source exceeds its text limit.')
        try:
            size = len(content.encode('utf-8'))
        except UnicodeError:
            raise ValueError('Syntax source must be valid UTF-8.') from None
        total += size
        if size > 256000 or total > 512000:
            raise ValueError('Syntax source exceeds its UTF-8 byte limit.')
        normalized.append(dict(path=path, content=content))
    if lint:
        directories = {}
        for row in normalized:
            parts = row['path'].split('/')
            if any(part.casefold() == 'venv' for part in parts) or any(part.endswith('.py') for part in parts[:-1]):
                raise ValueError('Private or file-shaped lint snapshot directory.')
            for depth in range(1, len(parts)):
                path = '/'.join(parts[:depth]); folded = path.casefold()
                if folded in seen or (folded in directories and directories[folded] != path):
                    raise ValueError('Lint paths have directory aliases or file/directory collisions.')
                directories[folded] = path
        if len(directories) + len(normalized) > 128:
            raise ValueError('Lint snapshot tree exceeds its entry bound.')
    result = {key: raw[key] for key in fields - {'files', 'tool'}}
    if lint:
        result['tool'] = dict(raw['tool'])
    result['files'] = normalized
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    if 'request_digest' in raw and raw['request_digest'] != digest:
        raise ValueError('Syntax request digest does not match its exact fields.')
    return {**result, 'request_digest': digest}


def _lint_tool(value):
    return (isinstance(value, dict) and set(value) == {'sha256', 'version'}
            and _matches(value['sha256'], r'[0-9a-f]{64}')
            and _matches(value['version'], r'[0-9]{1,3}\.[0-9]{1,4}\.[0-9]{1,4}'))


def _lint_text(value, *, empty=False):
    if (not isinstance(value, str) or (not value and not empty)
            or len(value) > LINT_MAX_MESSAGE_BYTES
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
        return False
    try:
        return len(value.encode('utf-8')) <= LINT_MAX_MESSAGE_BYTES
    except UnicodeError:
        return False


def _validate_lint_receipt(req, receipt):
    """Keep malformed/partial evidence separate from findings and tool failures.

    The fixed profile is E9,F / Python 3.11 / isolated / no cache / no fixes.
    Columns are one-based Unicode character positions with an exclusive end.
    Diagnostic order is canonical; file checks retain the request's file order.
    A signed unavailable terminal outcome is a failed *job*, not a source defect.
    Worker authentication and executable integrity remain the caller's duties.
    """
    result = dict(status='incomplete', reasons=[], supplemental=True, authorized=False,
                  request_digest=req['request_digest'], lint_status='unconfirmed',
                  binding_scope='Hash binding is not cryptographic authentication.',
                  acceptance_scope='Remote fixed-profile Ruff evidence is not Windows or runtime acceptance.')
    def invalid(reason):
        result['reasons'].append(reason)
        return result
    if not isinstance(receipt, dict) or set(receipt) != _LINT_RECEIPT:
        return invalid('Unexpected or missing lint receipt fields.')
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != 2:
        return invalid('Unsupported lint receipt schema.')
    if (any(receipt[key] != req[key] for key in ('job_id', 'device_id', 'worker_digest',
                                               'request_digest', 'operation', 'profile'))
            or not _lint_tool(receipt['tool']) or receipt['tool'] != req['tool']):
        return invalid('Lint receipt does not match the exact request, worker and tool.')
    state, status = receipt['state'], receipt['status']
    if (not isinstance(state, str) or state not in {'completed', 'failed', 'unconfirmed', 'deferred'}
            or not isinstance(status, str) or status not in {'passed', 'findings', 'tool_failed', 'unavailable'}
            or type(receipt['source_unchanged']) is not bool
            or not _lint_text(receipt['reason'], empty=True)):
        return invalid('Malformed lint outcome or reason.')
    checks, diagnostics = receipt['checks'], receipt['diagnostics']
    if (not isinstance(checks, list) or len(checks) != len(req['files'])
            or not isinstance(diagnostics, list) or len(diagnostics) > LINT_MAX_DIAGNOSTICS):
        return invalid('Lint coverage or diagnostic count exceeds its contract.')
    files = {row['path']: row['content'] for row in req['files']}
    for expected, row in zip(req['files'], checks):
        if (not isinstance(row, dict) or set(row) != {'path', 'passed'}
                or row['path'] != expected['path'] or type(row['passed']) is not bool):
            return invalid('Lint file coverage is changed, duplicated or noncanonical.')
    keys, affected = [], set()
    for row in diagnostics:
        if (not isinstance(row, dict) or set(row) != _DIAGNOSTIC
                or not isinstance(row['path'], str) or row['path'] not in files
                or not _matches(row['code'], r'(?:F[0-9]{3}|E9[0-9]{2}|invalid-syntax)')
                or not _lint_text(row['message'])
                or any(type(row[key]) is not int or row[key] < 1
                       for key in ('line', 'column', 'end_line', 'end_column'))):
            return invalid('Malformed, out-of-profile or unbound lint diagnostic.')
        lines = files[row['path']].split('\n')
        start, end = (row['line'], row['column']), (row['end_line'], row['end_column'])
        if (end < start or any(line > len(lines) or column > len(lines[line - 1].removesuffix('\r')) + 1
                               for line, column in (start, end))):
            return invalid('Lint diagnostic lies outside the reviewed source.')
        keys.append((row['path'], *start, *end, row['code'], row['message']))
        affected.add(row['path'])
    if keys != sorted(set(keys)):
        return invalid('Lint diagnostics are duplicated or noncanonical.')
    completed = status in {'passed', 'findings'}
    if completed:
        if (state != 'completed' or receipt['source_unchanged'] is not True or receipt['reason']
                or bool(diagnostics) != (status == 'findings')
                or any(row['passed'] != (row['path'] not in affected) for row in checks)):
            return invalid('Lint completion, findings and file checks disagree.')
    elif (state not in {'failed', 'unconfirmed', 'deferred'} or diagnostics
          or any(row['passed'] for row in checks) or not receipt['reason']
          or (status == 'tool_failed' and state != 'failed')):
        return invalid('Lint tool failure or unavailable outcome is inconsistent.')
    try:
        size = len(json.dumps(receipt, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return invalid('Lint receipt is not bounded UTF-8 JSON.')
    if size >= LINT_MAX_RECEIPT_BYTES:
        return invalid('Lint receipt exceeds its byte limit; truncation is not completion.')
    result['lint_status'] = status
    if state != 'completed':
        if state == 'failed':
            result['status'] = 'failed'
        return invalid('The lint tool failed or was unavailable; no clean source result is established.')
    result['status'] = 'passed' if status == 'passed' else 'failed'
    if status == 'findings':
        result['reasons'].append('The worker reported fixed-profile lint findings.')
    return result


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def assess_admission(request, facts, *, now):
    """Return {decision: ready|defer|reject, reasons: [str]} without side effects.

    now and observed_at are seconds from the same trusted clock. Local facts
    must not come from the job request. Unknown or future observations defer.
    These conservative reserves are admission estimates, not enforcement.
    """
    req = normalize_request(request)
    result = assess_resources(facts,device_id=req['device_id'],worker_digest=req['worker_digest'],now=now)
    if req['schema_version'] == 2:
        # 512 MiB enforced worker ceiling * 1.5 estimate + 512 MiB host reserve.
        # Resource advertisements stay operation-neutral; only actual lint
        # admission applies this cost. No resident-model memory credit is used.
        for key, minimum in (('available_ram_gib', 1.25), ('free_disk_gib', .5)):
            value = facts.get(key) if isinstance(facts, dict) else None
            if not _number(value) or value < minimum:
                result['reasons'].append(key + ' is unknown or below the fixed lint reserve.')
                if result['decision'] != 'reject':
                    result['decision'] = 'defer'
    return result


def assess_resources(facts, *, device_id, worker_digest, now):
    """Assess a trusted resource advertisement without constructing a job."""
    if not isinstance(facts, dict) or not _number(now):
        return dict(decision='defer', reasons=['Trusted local resource observations are unavailable.'])
    rejected, deferred = [], []
    if facts.get('enabled') is False:
        rejected.append('Remote syntax work is disabled locally.')
    elif facts.get('enabled') is not True:
        deferred.append('Local enablement is unknown.')
    for key in ('device_id', 'worker_digest'):
        value = facts.get(key)
        if value is None:
            deferred.append('Verified ' + key + ' is unavailable.')
        elif value != {'device_id':device_id,'worker_digest':worker_digest}[key]:
            rejected.append('Verified ' + key + ' does not match the request.')
    observed = facts.get('observed_at')
    if not _number(observed) or not 0 <= now - observed <= 30:
        deferred.append('Resource observations are missing, stale or future-dated.')
    for key, minimum in (('available_ram_gib', .5), ('free_disk_gib', .1)):
        value = facts.get(key)
        if not _number(value) or value < minimum:
            deferred.append(key + ' is unknown or below the fixed reserve.')
    cpu = facts.get('cpu_percent')
    if not _number(cpu) or not 0 <= cpu <= 70:
        deferred.append('CPU observation is unknown or exceeds the 70 percent ceiling.')
    if facts.get('plugged_in') is not True:
        deferred.append('Confirmed external power is required.')
    return dict(decision='reject' if rejected else 'defer' if deferred else 'ready',
                reasons=rejected + deferred)


def validate_receipt(request, receipt):
    """Validate bounded exact coverage; return supplemental evidence only.

    Receipt fields: schema_version, job_id, device_id, worker_digest,
    request_digest, state, checks. Checks are exact {path, passed: bool} rows.
    Terminal completed plus all passed can yield passed; malformed/binding/
    coverage failures yield incomplete, reported failure yields failed.
    Returned evidence never authorizes application or proves worker honesty.
    """
    req = normalize_request(request)
    if req['schema_version'] == 2:
        return _validate_lint_receipt(req, receipt)
    result = dict(status='incomplete', reasons=[], supplemental=True, authorized=False,
                  request_digest=req['request_digest'],
                  binding_scope='Hash binding is not cryptographic authentication.',
                  acceptance_scope='Remote Python syntax evidence is not Windows or runtime acceptance.')
    def invalid(reason):
        result['reasons'].append(reason)
        return result
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT:
        return invalid('Unexpected or missing receipt fields.')
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != 1:
        return invalid('Unsupported receipt schema.')
    if any(receipt[key] != req[key] for key in ('job_id','device_id','worker_digest','request_digest')):
        return invalid('Receipt does not match the exact request and worker identity.')
    state = receipt['state']
    if not isinstance(state, str) or state not in {'completed','running','unconfirmed','failed','deferred'}:
        return invalid('Unknown receipt state.')
    rows = receipt['checks']
    if not isinstance(rows, list) or len(rows) != len(req['files']):
        return invalid('Receipt has missing or extra file coverage.')
    expected, seen, passed = {row['path'] for row in req['files']}, set(), True
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'path','passed'}
                or not isinstance(row['path'], str) or row['path'] not in expected
                or row['path'] in seen or type(row['passed']) is not bool):
            return invalid('Receipt file coverage is changed, duplicated or malformed.')
        seen.add(row['path'])
        passed = passed and row['passed']
    if state == 'failed' or not passed:
        result['status'] = 'failed'
        return invalid('The worker reported a failed syntax check or job.')
    if state != 'completed':
        return invalid('The worker has not confirmed terminal completion.')
    result['status'] = 'passed'
    return result
