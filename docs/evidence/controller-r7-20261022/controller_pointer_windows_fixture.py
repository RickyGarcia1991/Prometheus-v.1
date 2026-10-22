"""Isolated Windows test provider; generated temp roots only. NOT a live adapter."""
import base64
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

CORE = Path('C:/Users/rolon/Documents/Codex/Workstation/Local/AgentReach-20261012/source/tools/Prometheus.HostAgent/controller_pointer_transaction.py')
spec = importlib.util.spec_from_file_location('pointer_core_fixture', CORE)
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(data, protect=True):
    lib = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data)
    entropy = ctypes.create_string_buffer(b'prometheus-isolated-pointer-provider-v1')
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    ent = Blob(len(entropy.raw)-1, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    name = 'CryptProtectData' if protect else 'CryptUnprotectData'
    fn = getattr(lib, name)
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(incoming), None, ctypes.byref(ent), None, None, 1, ctypes.byref(outgoing)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def write_new(path, data):
    with open(path, 'xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def replace(path, data):
    temp = path.with_name(path.name + '.tmp-' + secrets.token_hex(8))
    write_new(temp, data)
    os.replace(temp, path)
    if path.read_bytes() != data:
        raise ValueError('readback')


def package(release, payload):
    return core.Package(release, hashlib.sha256(payload).hexdigest())


def isolated_root(root):
    raw = Path(root)
    if not raw.is_absolute() or '..' in raw.parts or '.' in raw.parts:
        raise ValueError('Normalized absolute temporary path required')
    normalized = Path(os.path.normpath(str(raw)))
    temp = Path(tempfile.gettempdir()).resolve()
    if normalized == temp or not normalized.is_relative_to(temp):
        raise ValueError('Strict temporary descendant required')
    # Inspect the original chain before resolving or writing: resolving a junction
    # first would hide its presence. No fixture writes precede this check.
    for entry in (raw, *raw.parents):
        if entry.lstat().st_file_attributes & 0x400:
            raise ValueError('Reparse root')
    if raw.resolve() != normalized:
        raise ValueError('Redirected temporary root')
    return normalized


def setup(root):
    # Caller supplies a new directory beneath the system temp directory.
    root = isolated_root(root)
    if list(root.iterdir()):
        raise ValueError('Fresh system temporary root required')
    plan = core.Plan('a'*32, 'synthetic-host', 'synthetic-volume',
        b'{"controller_release":"Prometheus-Controller-v1.0.10","core":{"v":55},"preserved":[1,2]}',
        package('Prometheus-Controller-v1.0.11', b'candidate fixture'),
        package('Prometheus-Controller-v1.0.12', b'recovery fixture'))
    marker = {'root': str(root), 'nonce': secrets.token_hex(32), 'fixture_only': True}
    write_new(root/'fixture-root.json', core.canonical(marker))
    write_new(root/'fixture-plan.json', core.canonical(plan.record()))
    write_new(root/'key.dpapi', dpapi(secrets.token_bytes(32)))
    write_new(root/'pointer.json', plan.previous)
    write_new(root/'floor-primary.json', core.canonical({'version':'1.0.10'}))
    write_new(root/'floor-recovery.json', core.canonical({'version':'1.0.10'}))
    write_new(root/'candidate.payload', b'candidate fixture')
    write_new(root/'recovery.payload', b'recovery fixture')
    return marker['nonce']


class Provider:
    def __init__(self, root, nonce, fault=None):
        self.root = isolated_root(root)
        self.nonce = nonce
        self.fault = fault
        self.check_root()
        saved = json.loads(self.read('fixture-plan.json'))
        self.plan = core.Plan(saved['transaction'], saved['host'], saved['volume'], base64.b64decode(saved['previous']),
            core.Package(**saved['candidate']), core.Package(**saved['recovery']))
        self.key = dpapi(self.read('key.dpapi'), False)
        if len(self.key) != 32:
            raise ValueError('Invalid protected key')
    def check_root(self):
        # Fixed generated root; refuse any reparse components/entries, never follow journals.
        for entry in (self.root, *self.root.parents):
            if entry.lstat().st_file_attributes & 0x400:
                raise ValueError('Reparse root')
        for entry in self.root.iterdir():
            if entry.lstat().st_file_attributes & 0x400:
                raise ValueError('Reparse child')
        marker = json.loads((self.root/'fixture-root.json').read_bytes())
        if marker != {'root':str(self.root), 'nonce':self.nonce, 'fixture_only':True}:
            raise ValueError('Root binding')
    def read(self, name):
        p = self.root/name
        if p.stat().st_size > 2*1024*1024:
            raise ValueError('Bound exceeded')
        return p.read_bytes()
    def hit(self, label):
        if self.fault == label:
            # Immediate process death, no Python finally/cleanup handlers.
            os._exit(77)
    @contextlib.contextmanager
    def exclusive(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        handle = create(str(self.root/'exclusive.lock'), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            close(handle)
    def assert_context(self, host, volume):
        self.check_root()
        if (host,volume) != ('synthetic-host','synthetic-volume'):
            raise ValueError('Host/volume binding')
    def verify(self, pkg):
        # Hash fixture models immutable package closure; no certificate/signature claim.
        name = 'candidate.payload' if pkg == self.plan.candidate else 'recovery.payload'
        if pkg not in (self.plan.candidate,self.plan.recovery):
            raise ValueError('Unknown fixture package')
        if hashlib.sha256(self.read(name)).hexdigest() != pkg.manifest:
            raise ValueError('Source fixture changed')
        cached = self.root/(pkg.manifest+'.cache')
        if cached.exists() and hashlib.sha256(self.read(cached.name)).hexdigest() != pkg.manifest:
            raise ValueError('Cached fixture changed')
    def stage(self, pkg):
        self.hit('before-cache-'+pkg.version)
        path=self.root/(pkg.manifest+'.cache')
        if not path.exists():
            replace(path,self.read('candidate.payload' if pkg == self.plan.candidate else 'recovery.payload'))
        self.verify(pkg)
        self.hit('after-cache-'+pkg.version)
    def read_pointer(self): return self.read('pointer.json')
    def read_floor(self):
        rows=[json.loads(self.read(name)) for name in ('floor-primary.json','floor-recovery.json')]
        if any(set(row) != {'version'} for row in rows): raise ValueError('Malformed floor')
        return max((row['version'] for row in rows),key=core.version)
    def read_intent(self, tx):
        if tx != self.plan.transaction: raise ValueError('Unknown transaction')
        path=self.root/'intent.json'
        return self.read(path.name) if path.exists() else None
    def write_intent(self, tx, data):
        self.hit('before-intent'); write_new(self.root/'intent.json',data); self.hit('after-intent')
    def set_floor(self, value):
        if core.version(value) < core.version(self.read_floor()): raise ValueError('Downgrade')
        data=core.canonical({'version':value})
        self.hit('before-recovery-floor'); replace(self.root/'floor-recovery.json',data); self.hit('after-recovery-floor')
        replace(self.root/'floor-primary.json',data); self.hit('after-primary-floor')
    def replace_pointer(self, expected, desired):
        if self.read_pointer() != expected: raise ValueError('Pointer changed')
        self.hit('before-pointer'); replace(self.root/'pointer.json',desired); self.hit('after-pointer')
    def write_receipt(self, tx, data):
        parsed=json.loads(data)
        if tx != self.plan.transaction or parsed['manifest'] not in (self.plan.candidate.manifest,self.plan.recovery.manifest): raise ValueError('Receipt scope')
        path=self.root/('receipt-'+parsed['manifest']+'.json')
        self.hit('before-receipt')
        if path.exists():
            if self.read(path.name) != data: raise ValueError('Receipt changed')
        else: write_new(path,data)
        self.hit('after-receipt')

if __name__ == '__main__':
    # Test harness only: refuses non-temp roots and requires independently provided nonce.
    p = Provider(sys.argv[1],sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None)
    print(json.dumps(core.execute(p,p.plan,p.key,forward_recovery='--forward' in sys.argv)))
