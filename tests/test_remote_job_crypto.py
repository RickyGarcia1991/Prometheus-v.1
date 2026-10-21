import hashlib

import pytest

from prometheus_assistant.remote_job_crypto import certificate_verifier


@pytest.mark.parametrize('data,pin', [(b'', 'a'*64), (b'x'*16385, 'a'*64),
    ('not bytes','a'*64), (b'x','A'*64), (b'x','wrong'), (b'x','0'*64)])
def test_bad_pin_and_bounds_reject_before_optional_crypto(data, pin):
    with pytest.raises(ValueError, match='trusted pin'):
        certificate_verifier(data, pin)


def test_missing_crypto_is_explicit(monkeypatch):
    import builtins
    real = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'cryptography' or name.startswith('cryptography.'):
            raise ImportError('not installed')
        return real(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    with pytest.raises(RuntimeError, match='dependency is unavailable'):
        certificate_verifier(b'x', hashlib.sha256(b'x').hexdigest())
