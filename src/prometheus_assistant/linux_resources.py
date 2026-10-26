"""Small native adapters; shared Windows resources are never executable on Linux."""
import ctypes
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import threading

_ZIM_LOCK = threading.Lock()
_ZIM_PINS = {
    'usr/lib/x86_64-linux-gnu/libxapian.so.30.14.3': '392151c7574082d954e444938c5dbe86c432423e0331e54083a347201c5a8dd5',
    'usr/lib/x86_64-linux-gnu/libzim.so.9.2.3': '317023e393c0cb0bc3d6fc411b07b599d3517dd10dbc4c7901634b5ec5b85a66',
    'usr/lib/python3/dist-packages/libzim.cpython-314-x86_64-linux-gnu.so': '73c77f966b814d454cffc0907af71019ef3fdc59482a0e804b2dee037ce4e272',
}

def system_executable(value):
    """Accept fixed OS-managed executables, not user-writable PATH substitutes."""
    path = Path(value).resolve(strict=True)
    for item in (path, *path.parents):
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('The native executable must be owned and writable only by the operating system.')
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('The native executable is missing or not executable.')
    return path

def zim_files(root):
    """Verify every native object before loading; the locator supplies no trust."""
    base = Path(root).absolute()
    paths = []
    for relative, expected in _ZIM_PINS.items():
        path = base / relative
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ImportError('Linked native reader paths are not accepted.')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
            raise ImportError('Invalid native reader object.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ImportError('The native offline reader differs from its reviewed pins.')
        paths.append(path)
    return paths

def load_zim():
    """Load the Ubuntu 26.04 amd64/Python 3.14 reader, independently of bundled .pyd files."""
    with _ZIM_LOCK:
        existing = sys.modules.get('libzim')
        if existing is not None:
            return existing
        root = os.environ.get('PROMETHEUS_ZIM_RUNTIME')
        if not root or sys.version_info[:2] != (3, 14):
            raise ImportError('Configure the reviewed native offline reader for this Python version.')
        try:
            xapian, zim, extension = zim_files(root)
            # Exact pinned files first; their SONAMEs satisfy the extension's dependencies.
            ctypes.CDLL(str(xapian), mode=ctypes.RTLD_GLOBAL)
            ctypes.CDLL(str(zim), mode=ctypes.RTLD_GLOBAL)
            spec = importlib.util.spec_from_file_location('libzim', extension)
            module = importlib.util.module_from_spec(spec)
            sys.modules['libzim'] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop('libzim', None)
                raise
            return module
        except (OSError, ValueError) as error:
            raise ImportError('The reviewed native offline reader is unavailable.') from error
