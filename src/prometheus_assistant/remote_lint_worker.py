"""Bind the fixed lint executor to a separately reviewed worker bundle."""
import hashlib
from pathlib import Path

from .remote_job_protocol import normalize_request, validate_receipt, LINT_OPERATION, LINT_PROFILE
from .remote_job_store import _no_links
from .remote_job_wire import _load


def _read(path, limit):
    _no_links(path)
    if not path.is_file() or path.stat().st_size>=limit:
        raise ValueError('Missing or excessive reviewed lint material.')
    with path.open('rb') as stream: data=stream.read(limit)
    if len(data)>=limit: raise ValueError('Excessive reviewed lint material.')
    return data


def pinned_executor(root, request, *, runner=None):
    """Validate provisioned tool identity before any durable execution claim."""
    req=normalize_request(request)
    if req['operation']!=LINT_OPERATION: raise ValueError('Expected a lint operation.')
    root=Path(root)
    if not root.is_absolute(): raise ValueError('An absolute reviewed worker root is required.')
    manifest_raw=_read(root/'bundle-manifest.json',8192)
    if hashlib.sha256(manifest_raw).hexdigest()!=req['worker_digest']:
        raise ValueError('Reviewed lint worker identity changed.')
    manifest=_load(manifest_raw,8192)
    raw=_read(root/'ruff-tool.json',4096)
    if not isinstance(manifest,dict) or manifest.get('ruff-tool.json')!=hashlib.sha256(raw).hexdigest():
        raise ValueError('Lint tool manifest is not bound to the reviewed worker.')
    tool=_load(raw,4096)
    if (not isinstance(tool,dict) or set(tool)!={'schema_version','profile','tool','executable'}
            or type(tool['schema_version']) is not int or tool['schema_version']!=1
            or tool['profile']!=LINT_PROFILE or tool['profile']!=req['profile']
            or tool['tool']!=req['tool'] or tool['executable']!='tools/ruff'
            or manifest.get('tools/ruff')!=req['tool']['sha256']):
        raise ValueError('Requested lint tool differs from the reviewed installation.')
    executable=root/'tools/ruff'
    _no_links(executable)
    if not executable.is_file() or not 4<=executable.stat().st_size<=128*1024*1024:
        raise ValueError('Reviewed lint executable is missing or excessive.')
    digest=hashlib.sha256()
    with executable.open('rb') as stream:
        header=stream.read(4)
        if header!=b'\x7fELF': raise ValueError('Reviewed lint executable is not ELF.')
        digest.update(header);total=4
        while chunk:=stream.read(1024*1024):
            total+=len(chunk)
            if total>128*1024*1024: raise ValueError('Excessive lint executable.')
            digest.update(chunk)
    if digest.hexdigest()!=req['tool']['sha256']:
        raise ValueError('Reviewed lint executable changed before claim.')
    def execute(current):
        current=normalize_request(current)
        if current!=req: raise ValueError('The exact lint request changed.')
        if runner is None:
            from .remote_lint_runner import execute_linux_lint
            run=execute_linux_lint
        else: run=runner
        result=run(req['files'],tool_path=executable,tool_sha256=req['tool']['sha256'],
            tool_version=req['tool']['version'],profile=req['profile'],timeout_seconds=req['timeout_seconds'])
        affected={row['path'] for row in result['diagnostics']}
        receipt={key:req[key] for key in ('schema_version','job_id','device_id','worker_digest',
            'request_digest','operation','tool','profile')}
        receipt.update({key:result[key] for key in ('state','status','diagnostics','reason','source_unchanged')})
        receipt['checks']=[dict(path=row['path'],passed=result['state']=='completed' and row['path'] not in affected)
            for row in req['files']]
        if validate_receipt(req,receipt)['status']=='incomplete':
            raise ValueError('The lint runner did not return valid terminal evidence.')
        return receipt
    return execute
