"""Bounded read-only preflight entry point. Prints JSON; never writes live state."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from active_migration_preflight import evaluate, result

BASE = Path(__file__).resolve().parent
COLLECTOR = BASE / 'collect-active-host-migration-preflight.ps1'
COLLECTOR_SHA256 = '399606ea06f58a85c65c8d6c53a831c58bcfc893e35cbbb9ddeceafbff8fefcd'
PWSH = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'


def sessions_snapshot():
    """WTS session inventory and SID resolution, no session mutation operations."""
    wts = ctypes.WinDLL('wtsapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)

    class Session(ctypes.Structure):
        _fields_ = [('id', wintypes.DWORD), ('station', wintypes.LPWSTR), ('state', ctypes.c_int)]

    wts.WTSEnumerateSessionsW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Session)), ctypes.POINTER(wintypes.DWORD)]
    wts.WTSEnumerateSessionsW.restype = wintypes.BOOL
    wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    kernel.GetCurrentProcessId.restype = wintypes.DWORD
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.LookupAccountNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.LookupAccountNameW.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL

    def query(session, kind):
        buffer, length = ctypes.c_void_p(), wintypes.DWORD()
        if not wts.WTSQuerySessionInformationW(None, session, kind, ctypes.byref(buffer), ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if length.value > 4096:
                raise ValueError('Session string exceeds bound')
            return ctypes.wstring_at(buffer) if buffer.value else ''
        finally:
            if buffer.value:
                wts.WTSFreeMemory(buffer)

    def account_sid(account):
        sid_size, domain_size, use = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        advapi.LookupAccountNameW(None, account, None, ctypes.byref(sid_size), None, ctypes.byref(domain_size), ctypes.byref(use))
        if not 0 < sid_size.value <= 1024 or domain_size.value > 4096:
            raise ValueError('Session owner SID unavailable')
        sid = ctypes.create_string_buffer(sid_size.value)
        domain = ctypes.create_unicode_buffer(max(domain_size.value, 1))
        if not advapi.LookupAccountNameW(None, account, sid, ctypes.byref(sid_size), domain, ctypes.byref(domain_size), ctypes.byref(use)):
            raise ctypes.WinError(ctypes.get_last_error())
        text = ctypes.c_void_p()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(text)
        finally:
            kernel.LocalFree(text)

    current = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), ctypes.byref(current)):
        raise ctypes.WinError(ctypes.get_last_error())
    rows, count = ctypes.POINTER(Session)(), wintypes.DWORD()
    if not wts.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(rows), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    found = []
    try:
        if count.value > 128:
            raise ValueError('Session inventory exceeds bound')
        for index in range(count.value):
            row = rows[index]
            user = query(row.id, 5)
            if not user:
                continue
            domain = query(row.id, 7)
            found.append(dict(session_id=row.id, sid=account_sid((domain + '\\' if domain else '') + user), state={0: 'active', 4: 'disconnected'}.get(row.state, 'unsupported-' + str(row.state))))
    finally:
        wts.WTSFreeMemory(rows)
    return dict(complete=True, current_session=current.value, rows=found)


def inspect(runner=subprocess.run, session_collector=sessions_snapshot):
    try:
        if hashlib.sha256(COLLECTOR.read_bytes()).hexdigest() != COLLECTOR_SHA256:
            raise ValueError('Pinned inspect-only collector changed')
        sessions = session_collector()
        collected = runner([str(PWSH), '-NoProfile', '-NonInteractive', '-File', str(COLLECTOR)], capture_output=True, text=True, timeout=45)
        if collected.returncode or len(collected.stdout.encode()) > 1048576:
            raise ValueError('Native collector failed or exceeded output bound')
        observations = json.loads(collected.stdout)
        report = evaluate(observations, sessions)
        report['collector_sha256'] = COLLECTOR_SHA256
        return report
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return result('incomplete_context', [str(error)])


if __name__ == '__main__':
    if len(sys.argv) != 1:
        raise SystemExit('Inspect-only entry point accepts no mutation or context overrides')
    report = inspect()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['status'] == 'preflight_observations_valid' else 2)


