"""Bounded Linux syntax work behind a durable claim and trusted local admission.

This is a worker building block, not a remote endpoint or an authentication
mechanism. Its caller must authenticate an exact approved request and provide
local observations independently. Source is compiled in memory, never executed
or written to project files. Hash-bound receipts remain supplemental evidence.
"""
import json
import subprocess
import sys
import tempfile
import time

from .remote_job_protocol import normalize_request, assess_admission, validate_receipt
from .remote_job_envelope import open_envelope


# A fixed child program, never interpolated with request content. Limits are set
# inside the isolated child, avoiding preexec_fn in the threaded application.
_CHILD_CODE = r'''
import sys
if sys.platform != 'linux':
    raise SystemExit(2)
import resource
def ceiling(kind, wanted):
    soft, hard = resource.getrlimit(kind)
    limits = [wanted] + [v for v in (soft, hard) if v != resource.RLIM_INFINITY]
    bound = min(limits)
    resource.setrlimit(kind, (bound, bound))
ceiling(resource.RLIMIT_AS, 256 * 1024 * 1024)
ceiling(resource.RLIMIT_CPU, 5)
ceiling(resource.RLIMIT_FSIZE, 8192)
ceiling(resource.RLIMIT_CORE, 0)
ceiling(resource.RLIMIT_NOFILE, 32)
import json
import warnings
warnings.simplefilter('ignore')
payload = sys.stdin.buffer.read(3100001)
if len(payload) > 3100000:
    raise SystemExit(2)
request = json.loads(payload)
checks = []
for row in request['files']:
    passed = True
    try:
        compile(row['content'], row['path'], 'exec', dont_inherit=True, optimize=0)
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError, OverflowError):
        passed = False
    checks.append({'path': row['path'], 'passed': passed})
receipt = {key: request[key] for key in ('schema_version', 'job_id', 'device_id',
                                       'worker_digest', 'request_digest')}
receipt.update(state='completed', checks=checks)
sys.stdout.write(json.dumps(receipt, separators=(',', ':')))
'''


def _failed(req):
    return {**{key: req[key] for key in ('schema_version', 'job_id', 'device_id',
            'worker_digest', 'request_digest')}, 'state': 'failed',
            'checks': [dict(path=row['path'], passed=False) for row in req['files']]}


def execute_linux_syntax(request):
    """Run the fixed compiler once with Linux hard limits and a wall deadline.

    No code execution, imports from submitted source, shell, source-file writes,
    network requests or child process tree are part of this operation. This is
    not a sandbox for running arbitrary code. Runtime/library integrity and the
    authenticated transport remain the integrating caller's responsibilities.
    """
    req = normalize_request(request)
    if sys.platform != 'linux':
        raise RuntimeError('Remote syntax execution requires Linux resource limits.')
    payload = json.dumps(req, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > 3100000:
        raise ValueError('Encoded syntax request exceeds the transport bound.')
    with tempfile.TemporaryDirectory(prefix='prometheus-syntax-') as cwd:
        with tempfile.TemporaryFile() as output:
            try:
                child = subprocess.run([sys.executable, '-I', '-S', '-c', _CHILD_CODE],
                    input=payload, stdout=output, stderr=subprocess.DEVNULL,
                    cwd=cwd, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                    timeout=req['timeout_seconds'], close_fds=True, check=False)
            except subprocess.TimeoutExpired:
                # subprocess.run has killed and waited for its sole owned child.
                return _failed(req)
            if child.returncode:
                return _failed(req)
            output.seek(0)
            data = output.read(8193)
    if len(data) > 8192:
        raise ValueError('Syntax receipt exceeds its output bound.')
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise ValueError('The syntax process did not return a complete receipt.') from None
    if validate_receipt(req, result)['status'] == 'incomplete':
        raise ValueError('The syntax process returned an invalid receipt.')
    return result


def _result(req, entry, *, replayed):
    result = dict(state=entry['state'], receipt=entry['receipt'], replayed=replayed,
                  reasons=[], evidence=None)
    if entry['receipt'] is not None:
        result['evidence'] = validate_receipt(req, entry['receipt'])
    elif entry['state'] == 'unconfirmed':
        result['reasons'] = ['A durable claim exists without a confirmed result; this job will not run again.']
    return result


def run_job(request, store, observe, *, executor=None, clock=time.time):
    """Admit, claim, execute and persist one exact authorized job.

    observe() must read trusted local state, including the local enable/deny
    switch and the actual installed worker identity. It is never taken from the
    request. An injected executor is a test seam; production uses Linux limits.
    Claim/storage errors propagate before execution. Any uncertain result after
    the durable claim remains unconfirmed, including failure to persist output.
    """
    req = normalize_request(request)
    previous = store.lookup(req)
    if previous is not None:
        return _result(req, previous, replayed=True)
    admission = assess_admission(req, observe(), now=clock())
    if admission['decision'] != 'ready':
        return dict(state='rejected' if admission['decision'] == 'reject' else 'deferred',
                    receipt=None, replayed=False, evidence=None, reasons=admission['reasons'])
    if executor is None and sys.platform != 'linux':
        return dict(state='deferred', receipt=None, replayed=False, evidence=None,
                    reasons=['This worker requires Linux resource limits.'])
    claim = store.claim(req)
    if not claim['claimed']:
        return _result(req, claim, replayed=True)
    try:
        result = (executor or execute_linux_syntax)(req)
        checked = validate_receipt(req, result)
        if checked['status'] == 'incomplete' or result['state'] not in ('completed', 'failed'):
            raise ValueError('No confirmed terminal syntax result.')
        store.finish(req, result)
    except Exception:
        # Do not convert a partial/missing receipt into a terminal success or
        # retry. Process death and KeyboardInterrupt likewise leave the claim.
        return _result(req, dict(state='unconfirmed', receipt=None), replayed=False)
    return _result(req, dict(state=result['state'], receipt=result), replayed=False)


def run_signed_job(envelope, store, observe, verify, *, device_id, worker_digest,
                   executor=None, clock=time.time):
    """Authenticate a pinned-sender job before admission, storage or execution.

    Device identity, installed worker digest and certificate verifier must come
    from separately provisioned local configuration, never packet fields. Owner
    approval is enforced before signing by the controller. This call does not
    install a worker, enable local permission or authenticate returned receipts.
    """
    request = open_envelope(envelope, verify, device_id=device_id,
                            worker_digest=worker_digest, now=int(clock()))
    return run_job(request, store, observe, executor=executor, clock=clock)
