"""Pinned-certificate RSA-PSS verification for the optional Linux worker.

The certificate pin is local trusted configuration provisioned separately from
the incoming job. This module does not establish trust, enroll a sender, load a
private key or modify the machine certificate store. Missing cryptography fails
closed; the integrating worker must qualify that dependency before enablement.
"""
from datetime import datetime, timezone
import hashlib
import re


def certificate_verifier(certificate_der, expected_sha256, *, now=None):
    if (not isinstance(certificate_der, bytes) or not 1 <= len(certificate_der) <= 16384
            or not isinstance(expected_sha256, str)
            or re.fullmatch('[0-9a-f]{64}', expected_sha256) is None
            or hashlib.sha256(certificate_der).hexdigest() != expected_sha256):
        raise ValueError('The sender certificate does not match the local trusted pin.')
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError:
        raise RuntimeError('The worker certificate verification dependency is unavailable.') from None
    try:
        cert = x509.load_der_x509_certificate(certificate_der)
        key = cert.public_key()
    except (ValueError, TypeError):
        raise ValueError('The pinned sender certificate is malformed.') from None
    if not isinstance(key, rsa.RSAPublicKey) or not 2048 <= key.key_size <= 8192:
        raise ValueError('The pinned certificate must contain a supported RSA public key.')
    start = getattr(cert, 'not_valid_before_utc', None)
    end = getattr(cert, 'not_valid_after_utc', None)
    if start is None:
        start = cert.not_valid_before.replace(tzinfo=timezone.utc)
        end = cert.not_valid_after.replace(tzinfo=timezone.utc)
    clock = now or (lambda: datetime.now(timezone.utc))

    def verify(payload, signature):
        current = clock()
        if not isinstance(current, datetime) or current.tzinfo is None or not start <= current <= end:
            return False
        if (not isinstance(payload, bytes) or not 1 <= len(payload) <= 3100000
                or not isinstance(signature, bytes) or len(signature) != (key.key_size+7)//8):
            return False
        try:
            key.verify(signature, payload,
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                       hashes.SHA256())
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True
    return verify
