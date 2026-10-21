"""On-demand Linux worker, no daemon or enrollment.

The installer must protect this root with owner-only permissions and verify the
installed bundle against worker_digest. Configuration is trusted local state,
not proof of installed bytes. Private receipt keys never leave this process.
Unsigned status packets are informational, never terminal execution evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import shutil
import stat
import sys
import time

from .linux_power import read_power
from .remote_job_crypto import certificate_verifier
from .remote_job_envelope import open_envelope
from .remote_job_store import JobStore
from .remote_job_wire import decode_request, seal_receipt, _load, _dump, MAX_REQUEST_BYTES
from .remote_resource_wire import open_query, seal_advertisement, MAX_RESOURCE_BYTES
from .remote_syntax_worker import run_signed_job


def _plain(path):
    for item in (path,*path.parents):
        if item.exists() or item.is_symlink():
            info=item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Linked worker paths are not accepted.')


def _read(path,limit):
    _plain(path)
    if not path.is_file() or path.stat().st_size>limit:
        raise ValueError('Worker configuration material is missing or excessive.')
    with path.open('rb') as stream: data=stream.read(limit+1)
    if len(data)>limit: raise ValueError('Worker material exceeds its bound.')
    return data


def _config(root):
    config=_load(_read(root/'worker-config.json',4096),4097)
    fields={'schema_version','enabled','device_id','worker_digest',
            'controller_certificate_sha256','receipt_certificate_sha256'}
    if (type(config) is not dict or set(config)!=fields or type(config['schema_version']) is not int
            or config['schema_version']!=1 or type(config['enabled']) is not bool
            or not isinstance(config['device_id'],str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',config['device_id']) is None):
        raise ValueError('Malformed worker configuration.')
    for field in fields-{'schema_version','enabled','device_id'}:
        if not isinstance(config[field],str) or re.fullmatch('[0-9a-f]{64}',config[field]) is None:
            raise ValueError('Malformed worker identity pin.')
    return config


def _receipt_signer(root,config,clock):
    cert_bytes=_read(root/'receipt.cer',16384)
    if hashlib.sha256(cert_bytes).hexdigest()!=config['receipt_certificate_sha256']:
        raise ValueError('Receipt certificate pin mismatch.')
    key_bytes=_read(root/'receipt-key.pem',16384)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    cert=x509.load_der_x509_certificate(cert_bytes)
    key=serialization.load_pem_private_key(key_bytes,password=None)
    if (not isinstance(key,rsa.RSAPrivateKey) or not 2048<=key.key_size<=8192
            or not isinstance(cert.public_key(),rsa.RSAPublicKey)
            or key.public_key().public_numbers()!=cert.public_key().public_numbers()):
        raise ValueError('Receipt private key does not match the pinned certificate.')
    verify=certificate_verifier(cert_bytes,config['receipt_certificate_sha256'],
        now=lambda:datetime.fromtimestamp(clock(),timezone.utc))
    def sign(data):
        signature=key.sign(data,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
        if verify(data,signature) is not True: raise ValueError('Receipt certificate is not currently valid.')
        return signature
    sign(b'prometheus receipt key qualification')
    return sign


def _cpu():
    try:
        with open('/proc/stat','rb') as stream: line=stream.readline(1024)
        parts=line.decode('ascii').split()
        if parts[0]!='cpu': return None
        values=[int(value) for value in parts[1:9]]
        if len(values)!=8 or any(value<0 for value in values): return None
        return sum(values),values[3]+values[4]
    except (OSError,ValueError,IndexError): return None


def _observe(root,clock):
    ram=disk=cpu=None
    try:
        with open('/proc/meminfo','rb') as stream: data=stream.read(16385)
        if len(data)<=16384:
            match=re.search(rb'^MemAvailable:\s+(\d+) kB$',data,re.M)
            if match: ram=int(match[1])*1024/2**30
    except OSError: pass
    try: disk=shutil.disk_usage(root).free/2**30
    except OSError: pass
    first=_cpu(); time.sleep(.1); second=_cpu()
    if first and second:
        total=second[0]-first[0]; idle=second[1]-first[1]
        if total>0 and 0<=idle<=total: cpu=100*(total-idle)/total
    battery,plugged=read_power()
    return dict(observed_at=clock(),available_ram_gib=ram,free_disk_gib=disk,
                cpu_percent=cpu,battery_percent=battery,plugged_in=plugged)


def _status(state):
    return _dump(dict(schema_version=1,state=state,authenticated=False,receipt=None),1024)


def process_resource_query(data,root,*,clock=time.time,observe=None):
    """Return signed fresh observations without opening a ledger or claiming work.

    The separate resource purpose permits no source transfer or execution. Local
    enablement always comes from freshly checked configuration, including when
    disabled; missing telemetry remains null rather than implying capacity.
    """
    try:
        root=Path(root)
        if not root.is_absolute(): raise ValueError('Absolute worker root required.')
        _plain(root)
        config=_config(root)
        verify=certificate_verifier(_read(root/'controller.cer',16384),config['controller_certificate_sha256'],
            now=lambda:datetime.fromtimestamp(clock(),timezone.utc))
        now=clock()
        if type(now) not in (int,float) or not math.isfinite(now): raise ValueError('Invalid clock.')
        query=open_query(data,verify,device_id=config['device_id'],worker_digest=config['worker_digest'],now=int(now))
        facts=observe() if observe is not None else _observe(root,clock)
        if type(facts) is not dict: raise ValueError('Invalid resource observations.')
        fresh=_config(root)
        if fresh!=config: raise ValueError('Worker configuration changed during observation.')
        observations={name:facts.get(name) for name in ('available_ram_gib','free_disk_gib',
            'cpu_percent','battery_percent','plugged_in')}
        observations['enabled']=fresh['enabled']
        signer=_receipt_signer(root,fresh,clock)
        issued=clock()
        if type(issued) not in (int,float) or not math.isfinite(issued): raise ValueError('Invalid clock.')
        issued=int(issued)
        if not query['issued_at']<=issued<query['expires_at'] or _config(root)!=fresh:
            raise ValueError('Resource query or worker configuration is no longer current.')
        return seal_advertisement(query,observations,signer,issued_at=issued,
            expires_at=min(query['expires_at'],issued+30))
    except Exception:
        return _status('rejected')


def process_packet(data,root,*,query_only=False,clock=time.time,observe=None):
    """Process one bounded packet. Any internal error returns sanitized rejection.

    observe is a trusted test/platform seam returning resource observations only;
    local enablement and identity are always supplied from configuration.
    Query authenticates the original request and never creates or claims work.
    """
    claimed=False
    try:
        if type(query_only) is not bool: raise ValueError('Invalid query flag.')
        root=Path(root)
        if not root.is_absolute(): raise ValueError('Absolute worker root required.')
        _plain(root)
        config=_config(root)
        if not config['enabled']: return _status('rejected')
        envelope=decode_request(data)
        verify=certificate_verifier(_read(root/'controller.cer',16384),config['controller_certificate_sha256'],
            now=lambda:datetime.fromtimestamp(clock(),timezone.utc))
        now=clock()
        if type(now) not in (int,float) or not math.isfinite(now): raise ValueError('Invalid clock.')
        req=open_envelope(envelope,verify,device_id=config['device_id'],worker_digest=config['worker_digest'],now=int(now))
        executor=None
        if req['schema_version']==2:
            from .remote_lint_worker import pinned_executor
            executor=pinned_executor(root,req)
        signer=_receipt_signer(root,config,clock)
        store=JobStore(root/'jobs.sqlite3')
        if query_only:
            result=store.lookup(req)
            if result is None: return _status('unconfirmed')
        else:
            if sys.platform!='linux': return _status('deferred')
            def observations():
                fresh=_config(root)
                if fresh!=config: return dict(enabled=False)
                facts=(observe() if observe is not None else _observe(root,clock))
                if _config(root)!=fresh: return dict(enabled=False)
                return {**facts,'enabled':fresh['enabled'],'device_id':fresh['device_id'],
                        'worker_digest':fresh['worker_digest']}
            claimed=True  # Conservatively report uncertainty if execution/persistence raises.
            result=run_signed_job(envelope,store,observations,verify,device_id=config['device_id'],
                                  worker_digest=config['worker_digest'],clock=clock,executor=executor)
        if result.get('receipt') is not None:
            issued=int(clock())
            return seal_receipt(req,result['receipt'],signer,issued_at=issued,expires_at=issued+300)
        state=result['state']
        return _status(state if state in ('unconfirmed','deferred','rejected') else 'unconfirmed')
    except Exception:
        return _status('unconfirmed' if claimed else 'rejected')


def main(argv=None):
    parser=argparse.ArgumentParser(description='Process one authenticated bounded syntax packet.')
    parser.add_argument('--root',required=True)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--query',action='store_true')
    modes.add_argument('--resources',action='store_true')
    args=parser.parse_args(argv)
    data=sys.stdin.buffer.read(MAX_RESOURCE_BYTES if args.resources else MAX_REQUEST_BYTES)
    result=(process_resource_query(data,Path(args.root)) if args.resources else
            process_packet(data,Path(args.root),query_only=args.query))
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()


if __name__=='__main__': main()
