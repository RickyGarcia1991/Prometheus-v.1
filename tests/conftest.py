"""Keep automated Windows CLI checks from opening visible terminal windows."""
import os
import subprocess
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
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', HiddenPopen)
