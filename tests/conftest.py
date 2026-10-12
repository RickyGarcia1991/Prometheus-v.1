"""Keep automated Windows CLI checks from opening visible terminal windows."""
import os
import subprocess
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def background_test_children(monkeypatch):
    if os.name != 'nt':
        return
    original = subprocess.Popen

    class HiddenPopen(original):
        def __init__(self, *args, **kwargs):
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
            kwargs.setdefault('stdin', subprocess.DEVNULL)
            command = kwargs.get('args', args[0] if args else None)
            if isinstance(command, (list, tuple)) and command and Path(str(command[0])).name.lower() in {'powershell', 'powershell.exe'}:
                environment = dict(kwargs.get('env') or os.environ)
                environment['PSModulePath'] = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/Modules')
                kwargs['env'] = environment
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', HiddenPopen)
