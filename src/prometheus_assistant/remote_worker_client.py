"""Owner-configured, bounded Desktop Commander fixed-worker transport.

The target file is provisioned by an explicitly reviewed installation, never by
HTTP/model input. It pins both sides' public identities and the worker bundle.
No discovery, enrollment, automatic retry or installation occurs here.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import time

from .application_policy import require_dispatch
from .remote_access import account_id, parse_devices, validate_arguments
from .remote_job_envelope import seal_request
from .remote_job_protocol import normalize_request, assess_resources, assess_admission, LINT_PROFILE, _lint_tool
from .remote_job_store import JobStore, _no_links
from .remote_job_wire import encode_request, open_receipt, _load

MAX_TRANSPORT_BYTES = 64000


def host_root():
    value = os.environ.get('LOCALAPPDATA', '')
    if os.name != 'nt' or not value or not Path(value).is_absolute():
        raise ValueError('Worker control needs this computer\'s Windows profile.')
    return Path(value)/'PrometheusWorkers'


def read_bounded(path, limit):
    _no_links(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Reviewed worker configuration is missing or excessive.')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError('Worker configuration exceeds its limit.')
    return data


def configuration(root):
    value = _load(read_bounded(root/'worker-target.json',8192),8193)
    fields = {'schema_version','enabled','account_id','device_id','device_name','worker_digest',
              'linux_root','signing_library','signing_library_sha256',
              'controller_certificate_sha256','receipt_certificate_sha256'}
    lint = type(value) is dict and type(value.get('schema_version')) is int and value['schema_version'] == 2
    if lint:
        fields |= {'lint_tool', 'lint_profile'}
    if (type(value) is not dict or set(value) != fields or type(value['schema_version']) is not int
            or value['schema_version'] != (2 if lint else 1) or value['enabled'] is not True):
        raise ValueError('A reviewed and enabled worker installation is required.')
    if lint and (value['lint_profile'] != LINT_PROFILE or not _lint_tool(value['lint_tool'])):
        raise ValueError('An exact reviewed lint tool and fixed profile are required.')
    for key in ('account_id','device_id'):
        if not isinstance(value[key],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',value[key]):
            raise ValueError('Invalid reviewed worker account or device.')
    if not isinstance(value['device_name'],str) or not 1 <= len(value['device_name']) <= 160:
        raise ValueError('Invalid reviewed worker name.')
    for key in ('worker_digest','signing_library_sha256','controller_certificate_sha256','receipt_certificate_sha256'):
        if not isinstance(value[key],str) or not re.fullmatch('[0-9a-f]{64}',value[key]):
            raise ValueError('Invalid reviewed worker identity pin.')
    remote = value['linux_root']
    if (not isinstance(remote,str) or len(remote)>500 or not re.fullmatch(r'/[A-Za-z0-9_./-]+',remote)
            or any(part in ('','.', '..') for part in remote[1:].split('/'))
            or str(PurePosixPath(remote)) != remote or not remote.startswith('/home/')):
        raise ValueError('Use a reviewed absolute worker folder under /home.')
    if not isinstance(value['signing_library'],str) or not Path(value['signing_library']).is_absolute():
        raise ValueError('Use an absolute reviewed local signing helper.')
    return value


class OwnerLedger:
    """Preserve the approved request for read-only recovery across host restarts."""
    def __init__(self, root, owner, *, authority_digest=None):
        if not isinstance(owner,str) or not re.fullmatch('[0-9a-f]{64}',owner):
            raise ValueError('A verified owner is required.')
        self.root = Path(root)/'owners'/owner
        if authority_digest is not None and (not isinstance(authority_digest,str) or not re.fullmatch('[0-9a-f]{64}',authority_digest)):
            raise ValueError('Invalid reviewed worker authority identity.')
        self.authority_digest = authority_digest
        _no_links(self.root)
        self.store = JobStore(self.root/'jobs.sqlite3')

    def _path(self, job_id):
        if not isinstance(job_id,str) or not re.fullmatch('[0-9a-f]{32}',job_id):
            raise ValueError('Invalid worker job identifier.')
        path = self.root/'requests'/(job_id+'.json')
        _no_links(path)
        return path

    def request(self, job_id):
        record = _load(read_bounded(self._path(job_id),MAX_TRANSPORT_BYTES),MAX_TRANSPORT_BYTES+1)
        if (not isinstance(record,dict) or set(record)!={'request','authority_digest'}
                or record['authority_digest']!=self.authority_digest):
            raise ValueError('Worker authority changed; this retained source cannot be sent to the new configuration.')
        return normalize_request(record['request'])

    def claim(self, req):
        req = normalize_request(req)
        path = self._path(req['job_id'])
        raw = json.dumps(dict(request=req,authority_digest=self.authority_digest),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        if len(raw)>MAX_TRANSPORT_BYTES:
            raise ValueError('The selected source exceeds the remote transport budget.')
        path.parent.mkdir(parents=True,exist_ok=True)
        _no_links(path)
        # Publish a complete immutable record before claiming or signing. A
        # failed publication cannot dispatch; a retained claim never reexecutes.
        import tempfile
        fd, name = tempfile.mkstemp(prefix='.request-',dir=path.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            try: os.link(temp,path)
            except FileExistsError:
                if self.request(req['job_id']) != req:
                    raise ValueError('Stored worker request differs from this review.')
        finally: temp.unlink(missing_ok=True)
        return self.store.claim(req)

    def lookup(self, req): return self.store.lookup(req)
    def finish(self, req, receipt): return self.store.finish(req,receipt)


class RemoteWorkerClient:
    def __init__(self, remote, *, root=None, clock=time.time, signer_factory=None, verifier_factory=None):
        self.remote, self.root, self.clock = remote, Path(root) if root is not None else host_root(), clock
        if not self.root.is_absolute(): raise ValueError('Worker host storage must be absolute.')
        self.signer_factory, self.verifier_factory = signer_factory, verifier_factory
        self._pinned_config = None

    def _config(self):
        current=configuration(self.root)
        if self._pinned_config is not None and self._pinned_config != current:
            raise ValueError('The reviewed worker configuration changed; prepare a new review.')
        self._pinned_config = dict(current)
        return current

    def authority_digest(self):
        return hashlib.sha256(json.dumps(self._config(),sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def lint_tool(self):
        """Return only locally provisioned fixed-profile capability, never discovery.

        The configuration digest and worker bundle pin bind this descriptor.
        An older syntax-only installation has no lint capability.
        """
        config = self._config()
        return dict(config['lint_tool']) if config['schema_version'] == 2 else None

    def _bound_request(self, request, config):
        req = normalize_request(request)
        if req['device_id'] != config['device_id'] or req['worker_digest'] != config['worker_digest']:
            raise ValueError('Reviewed request does not match the installed worker.')
        if req['schema_version'] == 2 and (config['schema_version'] != 2
                or req['tool'] != config['lint_tool'] or req['profile'] != config['lint_profile']):
            raise ValueError('Reviewed lint request does not match the provisioned tool capability.')
        return req

    def target(self):
        config = self._config()
        with self.remote.lock:
            device = self.remote._selected_device()
            if (self.remote.account_id != config['account_id'] or device['id'] != config['device_id']
                    or device['name'] != config['device_name']):
                raise ValueError('Select the exact reviewed worker account and computer.')
            return {key:config[key] for key in ('account_id','device_id','device_name','worker_digest')} | {
                'online':True,'configuration_digest':self.authority_digest()}

    def signer(self, data):
        config = self._config()
        factory = self.signer_factory
        if factory is None:
            from .remote_windows_crypto import request_signer
            factory = request_signer
        return factory(Path(config['signing_library']),config['signing_library_sha256'],
                       config['controller_certificate_sha256'])(data)

    def dispatch(self, envelope, *, query_only=False):
        config = self._config()
        self.target()
        req = self._bound_request(envelope['payload']['request'], config)
        packet = encode_request(envelope)
        if len(packet)>MAX_TRANSPORT_BYTES:
            raise ValueError('The selected source exceeds the remote transport budget.')
        tool = 'prometheus.lint' if req['schema_version'] == 2 else 'prometheus.syntax'
        data, verify = self._call_worker(packet,config,flag=' --query' if query_only else '',
            scope=dict(account=config['account_id'],device=config['device_id'],
                tool=tool+'.query' if query_only else tool,request_digest=req['request_digest']))
        return open_receipt(req,data,verify,device_id=config['device_id'],
                            worker_digest=config['worker_digest'],now=int(self.clock()))

    def resources(self):
        """Read one fresh signed observation; transfer no source and claim no job."""
        from .remote_resource_wire import seal_query, open_advertisement
        require_dispatch('remote.action')
        config = self._config()
        self.target()
        nonce, now = secrets.token_hex(16), int(self.clock())
        scope = dict(account=config['account_id'],device=config['device_id'],tool='prometheus.resources')
        require_dispatch('remote.dispatch',scope=scope)
        factory = self.signer_factory
        if factory is None:
            from .remote_windows_crypto import resource_query_signer
            factory = resource_query_signer
        signer = factory(Path(config['signing_library']),config['signing_library_sha256'],
                         config['controller_certificate_sha256'])
        packet = seal_query(signer,nonce=nonce,device_id=config['device_id'],
            worker_digest=config['worker_digest'],issued_at=now,expires_at=now+30)
        data, verify = self._call_worker(packet,config,flag=' --resources',scope=scope)
        return open_advertisement(data,verify,nonce=nonce,device_id=config['device_id'],
            worker_digest=config['worker_digest'],now=int(self.clock()))

    def admission(self, request=None):
        req = normalize_request(request) if request is not None else None
        if req is not None and req['schema_version'] == 2:
            self._bound_request(req, self._config())
        advertisement = self.resources()
        target = self.target()
        if req is not None:
            if any(req[key]!=target[key] for key in ('device_id','worker_digest')):
                raise ValueError('Resource observations do not match the reviewed job.')
        facts = {**advertisement['observations'],'observed_at':advertisement['issued_at'],
                 'device_id':target['device_id'],'worker_digest':target['worker_digest']}
        now = self.clock()
        if not advertisement['issued_at'] <= now < advertisement['expires_at']:
            raise ValueError('Resource observations expired before admission.')
        report = (assess_admission(req, facts, now=now) if req is not None and req['schema_version'] == 2
                  else assess_resources(facts,device_id=target['device_id'],worker_digest=target['worker_digest'],now=now))
        return {**report,'authenticated':True,'observed_at':advertisement['issued_at'],
            'expires_at':advertisement['expires_at'],'observations':advertisement['observations']}

    def _call_worker(self, packet, config, *, flag, scope):
        """Sole bounded transport for the installed worker's fixed operations."""
        if flag not in ('',' --query',' --resources') or len(packet)>MAX_TRANSPORT_BYTES:
            raise ValueError('Invalid bounded worker operation.')
        encoded = base64.b64encode(packet).decode('ascii')
        folder = config['linux_root']
        command = ("printf '%s' '"+encoded+"' | /usr/bin/base64 --decode | /usr/bin/python3 -I '"+
                   folder+"/run-worker.py' --root '"+folder+"'"+flag)
        with self.remote.lock:
            client = self.remote.client
            generation = self.remote.generation
            spec = next((s for s in self.remote.tools if s['name']=='start_process'),None)
            if client is None or spec is None: raise ValueError('The connected provider cannot run the reviewed worker.')
            arguments = dict(deviceId=config['device_id'],command=command,timeout_ms=3000)
            validate_arguments(arguments,spec['inputSchema'])
        # Read-only identity and online checks before the sole dispatch.
        identity = account_id(client.request('tools/call',dict(name='who_am_i',arguments={})))
        devices = parse_devices(client.request('tools/call',dict(name='list_devices',arguments={})))
        if identity != config['account_id'] or not any(d['id']==config['device_id'] and d['status']=='online' for d in devices):
            raise ValueError('The reviewed worker account or online target changed.')
        def current():
            if generation != self.remote.generation or client is not self.remote.client or self._config()!=config:
                raise ValueError('The reviewed worker connection changed.')
            self.target()
            require_dispatch('remote.dispatch',scope=scope)
        if flag==' --resources':
            # No source or execution authority is in this packet. Let the user
            # disconnect/select a device while the observation is in flight.
            with self.remote.lock: current()
            result = client.request('tools/call',dict(name='start_process',arguments=arguments),timeout=40)
            with self.remote.lock: current()
        else:
            with self.remote.lock:
                current()
                result = client.request('tools/call',dict(name='start_process',arguments=arguments),timeout=40)
                current()
        if not isinstance(result,dict) or result.get('isError'):
            raise ValueError('Worker outcome unconfirmed; the job will not be retried.')
        content = result.get('content')
        if not isinstance(content,list) or len(content)>10:
            raise ValueError('Invalid worker response.')
        texts = [row.get('text','') for row in content if isinstance(row,dict) and row.get('type')=='text']
        if any(not isinstance(t,str) for t in texts) or sum(len(t.encode()) for t in texts)>24000:
            raise ValueError('Worker response exceeds its bound.')
        # The bridge wraps stdout with process metadata. Only a unique complete
        # JSON line is eligible; signature verification establishes its origin.
        candidates = [line.encode() for text in texts for line in text.splitlines() if line.startswith('{') and line.endswith('}')]
        if len(candidates)!=1: raise ValueError('No unique signed worker response was returned.')
        cert = read_bounded(self.root/'receipt.cer',16384)
        factory = self.verifier_factory
        if factory is None:
            from .remote_windows_crypto import certificate_verifier, resource_certificate_verifier
            factory = resource_certificate_verifier if flag==' --resources' else certificate_verifier
        verify = factory(cert,config['receipt_certificate_sha256'])
        return candidates[0], verify

    def recover(self, req, ledger):
        req = self._bound_request(req, self._config())
        if ledger.authority_digest != self.authority_digest():
            raise ValueError('Recovery must use the originally approved worker authority.')
        if ledger.request(req['job_id']) != normalize_request(req):
            raise ValueError('Recovery must use the exact retained approved request.')
        existing = ledger.lookup(req)
        if existing is None: raise ValueError('No approved claim exists for this job.')
        if existing.get('receipt') is not None: return existing
        now = int(self.clock())
        envelope = seal_request(req,self.signer,issued_at=now,expires_at=now+300)
        receipt = self.dispatch(envelope,query_only=True)
        return ledger.finish(req,receipt)
