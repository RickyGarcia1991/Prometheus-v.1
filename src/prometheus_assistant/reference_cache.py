"""Reuse checks only while a Windows file handle denies writes and replacement."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib,os

_session=ContextVar('verified_reference_session',default=None)

@contextmanager
def reference_session():
    if _session.get() is not None:
        yield;return
    cache={};token=_session.set(cache)
    try:yield
    finally:
        for stream in cache.values():stream.close()
        _session.reset(token)

def _locked_reader(path):
    import ctypes,msvcrt
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW
    create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype=wintypes.HANDLE
    handle=create(str(path),0x80000000,1,None,3,0x80,None) # Read, share-read only
    if handle==wintypes.HANDLE(-1).value:raise OSError(ctypes.get_last_error(),'Cannot obtain a read-only reference lock.')
    try:fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
    except Exception:
        kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle);raise
    return os.fdopen(fd,'rb')

def verify_reference(path,expected):
    cache=_session.get();key=(str(path),expected)
    if cache is not None and key in cache:return
    stream=_locked_reader(path) if os.name=='nt' and cache is not None else path.open('rb')
    keep=False
    try:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
            raise ValueError('Source reference checksum mismatch; preserve the file for review.')
        if os.name=='nt' and cache is not None:cache[key]=stream;keep=True
    finally:
        if not keep:stream.close()
