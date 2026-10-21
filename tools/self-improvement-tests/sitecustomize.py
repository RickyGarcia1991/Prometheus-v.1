"""Regression isolation guard, not an OS sandbox for hostile native code."""
import ipaddress
import json
import os
import sys

_log = os.environ.get('PROMETHEUS_TEST_GUARD_LOG', '')
_storage = os.path.normcase(os.path.abspath(os.environ.get('PROMETHEUS_TEST_STORAGE_ROOT', '/__not_configured__')))
_python = os.path.normcase(os.path.abspath(sys.executable))
_runtime = os.path.dirname(_python)
_scratch = os.path.normcase(os.path.abspath(os.environ.get('PROMETHEUS_TEST_SCRATCH', '/__not_configured__')))


def _within(value, root):
    return value == root or value.startswith(root.rstrip(os.sep) + os.sep)


def _deny(event, reason):
    if _log:
        with open(_log, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': event, 'reason': reason, 'pid': os.getpid()}) + '\n')
    raise PermissionError('Regression isolation: ' + reason)


def _path(value):
    if isinstance(value, (str, bytes)):
        return os.path.normcase(os.path.abspath(os.fsdecode(value)))
    return ''


def _audit(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and len(address) >= 2:
            host, port = address[:2]
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == 'localhost'
            if local and port not in {11434, 54555}:
                return
        _deny(event, 'external or live application network endpoint')
    elif event in {'open', 'os.listdir', 'os.scandir', 'os.remove', 'os.rmdir', 'os.mkdir'}:
        path = _path(args[0])
        writing = event in {'os.remove', 'os.rmdir', 'os.mkdir'}
        if event == 'open':
            mode, flags = args[1:3]
            writing = ((isinstance(mode, str) and any(char in mode for char in 'wax+'))
                or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        if path and _within(path, _storage):
            if not writing and _within(path, _runtime):
                return
            _deny(event, 'live portable storage access outside the read-only Python runtime')
    elif event in {'os.rename', 'os.replace', 'os.link', 'os.symlink'}:
        if any(_within(_path(path), _storage) for path in args[:2] if isinstance(path, (str, bytes))):
            _deny(event, 'live portable storage mutation')
    elif event == 'subprocess.Popen':
        path = _path(args[0])
        if path and _within(path, _storage) and path != _python:
            _deny(event, 'live portable executable other than the test Python runtime')
    elif event == 'ctypes.dlopen':
        path = _path(args[0])
        if path and _within(path, _storage) and not _within(path, _runtime):
            _deny(event, 'native library from live portable storage')


sys.addaudithook(_audit)
