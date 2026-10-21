"""Host-local PowerShell 7 RSA-PSS bridge; never exports a private key.

The signing library and controller certificate pins are trusted caller config.
The helper must be covered by the installed application's integrity checks.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .remote_job_envelope import _payload, _canonical
from .remote_job_store import _no_links
from .remote_job_wire import _load, _dump
from .remote_resource_wire import (
    _payload as _resource_payload, _QUERY, _ADVERTISEMENT, MAX_RESOURCE_BYTES,
)


def _pin(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('A trusted SHA256 pin is required.')


def _library(path,pin):
    path=Path(path)
    _pin(pin)
    if not path.is_absolute(): raise ValueError('Absolute signing library required.')
    _no_links(path)
    if not path.is_file() or path.stat().st_size>262144 or hashlib.sha256(path.read_bytes()).hexdigest()!=pin:
        raise ValueError('Signing library does not match its trusted pin.')
    return path


def _invoke(packet):
    if os.name!='nt': raise RuntimeError('Windows cryptography bridge requires Windows.')
    executable=shutil.which('pwsh.exe')
    if not executable: raise RuntimeError('PowerShell 7 is required for the local signing identity.')
    _no_links(Path(executable))
    helper=Path(__file__).resolve().parents[2]/'tools/remote-worker-crypto.ps1'
    _no_links(helper)
    if not helper.is_file(): raise RuntimeError('Signed runtime crypto helper is unavailable.')
    with tempfile.TemporaryDirectory(prefix='prometheus-crypto-') as folder:
        source=Path(folder)/'input.json'; output=Path(folder)/'output.json'
        source.write_text(json.dumps(packet,separators=(',',':')),encoding='utf-8')
        try:
            completed=subprocess.run([executable,'-NoProfile','-NonInteractive','-File',str(helper),
                '-InputPath',str(source),'-OutputPath',str(output)],shell=False,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30,check=False)
            if completed.returncode or not output.is_file() or output.stat().st_size>4096:
                raise ValueError('Cryptographic helper failed.')
            result=_load(output.read_bytes(),4097)
            if type(result) is not dict: raise ValueError('Invalid crypto helper response.')
            return result
        except (OSError,subprocess.TimeoutExpired):
            raise ValueError('Cryptographic helper unavailable or timed out.') from None


def request_signer(library_path,library_sha256,controller_cert_sha256):
    """Return signer(bytes) restricted to canonical fresh syntax/lint payloads."""
    path=_library(library_path,library_sha256); _pin(controller_cert_sha256)
    def sign(data):
        _library(path,library_sha256)
        payload=_payload(_load(data,3100001))
        now=int(time.time())
        if _canonical(payload)!=data or not payload['issued_at']<=now<payload['expires_at']:
            raise ValueError('Only canonical current fixed work requests can be signed.')
        result=_invoke(dict(action='sign',payload=base64.b64encode(data).decode('ascii'),
            library_path=str(path),library_sha256=library_sha256,certificate_sha256=controller_cert_sha256))
        if set(result)!={'signature'} or not isinstance(result['signature'],str):
            raise ValueError('Invalid signer response.')
        try: signature=base64.b64decode(result['signature'],validate=True)
        except ValueError: raise ValueError('Invalid signer response.') from None
        if not 256<=len(signature)<=1024: raise ValueError('Unsupported signature length.')
        return signature
    return sign


def certificate_verifier(der,expected_sha256):
    """Return pinned receipt verifier(bytes, bytes)->bool using local .NET RSA."""
    _pin(expected_sha256)
    if type(der) is not bytes or not 1<=len(der)<=16384 or hashlib.sha256(der).hexdigest()!=expected_sha256:
        raise ValueError('Receipt certificate does not match its trusted pin.')
    def verify(payload,signature):
        try:
            value=_load(payload,16384)
            if (type(value) is not dict or value.get('purpose') not in
                    ('prometheus.remote.syntax.receipt', 'prometheus.remote.lint.receipt')
                    or type(signature) is not bytes or not 256<=len(signature)<=1024): return False
            if value['purpose'] == 'prometheus.remote.lint.receipt' and (
                    type(value.get('schema_version')) is not int or value['schema_version'] != 2
                    or not isinstance(value.get('receipt'), dict)
                    or type(value['receipt'].get('schema_version')) is not int
                    or value['receipt']['schema_version'] != 2
                    or value['receipt'].get('operation') != 'python-ruff-check-v1'):
                return False
            result=_invoke(dict(action='verify',payload=base64.b64encode(payload).decode('ascii'),
                signature=base64.b64encode(signature).decode('ascii'),certificate=base64.b64encode(der).decode('ascii'),
                certificate_sha256=expected_sha256))
            return set(result)=={'verified'} and result['verified'] is True
        except (ValueError,RuntimeError,OSError): return False
    return verify


def resource_query_signer(library_path,library_sha256,controller_cert_sha256):
    """Return signer(bytes) restricted to canonical current resource queries.

    This is deliberately separate from syntax request signing: an observation
    query cannot contain source or confer execution authority.
    """
    path=_library(library_path,library_sha256); _pin(controller_cert_sha256)
    def sign(data):
        _library(path,library_sha256)
        payload=_resource_payload(_load(data,MAX_RESOURCE_BYTES),_QUERY)
        now=int(time.time())
        if _dump(payload,MAX_RESOURCE_BYTES)!=data or not payload['issued_at']<=now<payload['expires_at']:
            raise ValueError('Only canonical current resource queries can be signed.')
        result=_invoke(dict(action='sign_resources',payload=base64.b64encode(data).decode('ascii'),
            library_path=str(path),library_sha256=library_sha256,certificate_sha256=controller_cert_sha256))
        if set(result)!={'signature'} or not isinstance(result['signature'],str):
            raise ValueError('Invalid resource signer response.')
        try: signature=base64.b64decode(result['signature'],validate=True)
        except ValueError: raise ValueError('Invalid resource signer response.') from None
        if not 256<=len(signature)<=1024: raise ValueError('Unsupported signature length.')
        return signature
    return sign


def resource_certificate_verifier(der,expected_sha256):
    """Return pinned verifier restricted to fresh resource advertisements."""
    _pin(expected_sha256)
    if type(der) is not bytes or not 1<=len(der)<=16384 or hashlib.sha256(der).hexdigest()!=expected_sha256:
        raise ValueError('Resource certificate does not match its trusted pin.')
    def verify(payload,signature):
        try:
            value=_resource_payload(_load(payload,MAX_RESOURCE_BYTES),_ADVERTISEMENT)
            now=int(time.time())
            if (_dump(value,MAX_RESOURCE_BYTES)!=payload or not value['issued_at']<=now<value['expires_at']
                    or type(signature) is not bytes or not 256<=len(signature)<=1024): return False
            result=_invoke(dict(action='verify_resources',payload=base64.b64encode(payload).decode('ascii'),
                signature=base64.b64encode(signature).decode('ascii'),certificate=base64.b64encode(der).decode('ascii'),
                certificate_sha256=expected_sha256))
            return set(result)=={'verified'} and result['verified'] is True
        except (ValueError,RuntimeError,OSError): return False
    return verify
