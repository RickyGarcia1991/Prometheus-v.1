"""Development-only controller pointer transaction. No filesystem/host adapter or CLI.

Provider methods execute under exclusive(): assert_context(host, volume), verify(package),
read_pointer(), read_floor(), read_intent(id), write_intent(id, bytes), stage(package),
set_floor(version), replace_pointer(expected, desired), write_receipt(id, bytes).
Intent writes and pointer replacement must be atomic, flushed and create-only/CAS;
verify checks immutable signed source AND staged closure. The adapter must protect the
HMAC key outside editable journals. No launch authority is conveyed by this core.
stage MUST NOT advance the floor or call Sync-Controller. Repeated stage/set_floor
must be idempotent. Receipts are immutable per transaction AND target manifest,
so forward recovery retains the earlier candidate completion record.
"""
import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('Invalid version')
    return tuple(map(int, value.split('.')))


def metadata(raw):
    if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
        raise ValueError('Metadata bound')
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('Duplicate metadata field')
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=pairs)
    def strict(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Nonfinite metadata number')
        if isinstance(value, str):
            value.encode('utf-8', errors='strict')
        if isinstance(value, dict):
            for key, child in value.items():
                strict(key)
                strict(child)
        if isinstance(value, list):
            for child in value:
                strict(child)
    strict(result)
    if not isinstance(result, dict) or not isinstance(result.get('controller_release'), str):
        raise ValueError('Invalid metadata')
    return result


@dataclass(frozen=True)
class Package:
    release: str
    manifest: str
    def __post_init__(self):
        if not self.release.startswith('Prometheus-Controller-v'):
            raise ValueError('Invalid release')
        version(self.release[len('Prometheus-Controller-v'):])
        if not re.fullmatch('[0-9a-f]{64}', self.manifest):
            raise ValueError('Invalid manifest')
    @property
    def version(self):
        return self.release[len('Prometheus-Controller-v'):]
    def record(self):
        return {'release': self.release, 'manifest': self.manifest}


@dataclass(frozen=True)
class Plan:
    transaction: str
    host: str
    volume: str
    previous: bytes
    candidate: Package
    recovery: Package
    def record(self):
        if not re.fullmatch('[0-9a-f]{32}', self.transaction):
            raise ValueError('Invalid transaction')
        if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (self.host, self.volume)):
            raise ValueError('Invalid binding')
        metadata(self.previous)
        if (version(self.recovery.version) <= version(self.candidate.version)
                or self.recovery.manifest == self.candidate.manifest):
            raise ValueError('Recovery must be strictly forward and distinct')
        return {'schema': 1, 'transaction': self.transaction, 'host': self.host,
                'volume': self.volume, 'previous': base64.b64encode(self.previous).decode(),
                'candidate': self.candidate.record(), 'recovery': self.recovery.record(),
                'candidate_pointer': base64.b64encode(self.pointer(self.candidate)).decode(),
                'recovery_pointer': base64.b64encode(self.pointer(self.recovery)).decode()}
    def pointer(self, package):
        obj = metadata(self.previous)
        obj['controller_release'] = package.release
        return canonical(obj)


def execute(provider, plan, key, *, forward_recovery=False):
    """Commit/resume exact plan. A caller must requalify inputs, not trust a journal.

    Forward recovery is explicit. Once its pointer is committed it cannot be moved
    back to candidate. Completion means pointer/cache/floor only, never launched.
    """
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('Protected authentication key required')
    body = canonical(plan.record())
    tag = hmac.new(key, b'controller-pointer-v1\0' + body, hashlib.sha256).hexdigest()
    envelope = canonical({'body': base64.b64encode(body).decode(), 'tag': tag})
    with provider.exclusive():
        provider.assert_context(plan.host, plan.volume)
        provider.verify(plan.candidate)
        provider.verify(plan.recovery)
        existing = provider.read_intent(plan.transaction)
        current = provider.read_pointer()
        candidate_pointer = plan.pointer(plan.candidate)
        recovery_pointer = plan.pointer(plan.recovery)
        if existing is None:
            if current != plan.previous:
                raise ValueError('Prior pointer changed')
            provider.write_intent(plan.transaction, envelope)
        else:
            # Exact authenticated envelope also binds the caller's entire plan.
            if not isinstance(existing, bytes) or not hmac.compare_digest(existing, envelope):
                raise ValueError('Intent authentication/binding failed')
        if provider.read_intent(plan.transaction) != envelope:
            raise ValueError('Intent readback failed')
        if current not in (plan.previous, candidate_pointer, recovery_pointer):
            raise ValueError('Pointer changed')
        target = plan.recovery if forward_recovery else plan.candidate
        if current == recovery_pointer:
            target = plan.recovery
        floor = provider.read_floor()
        if version(floor) > version(target.version):
            raise ValueError('Target below protected floor; explicit forward recovery required')
        # Recovery must be available before advancing any floor.
        provider.stage(plan.recovery)
        provider.verify(plan.recovery)
        provider.stage(target)
        provider.verify(target)
        provider.assert_context(plan.host, plan.volume)
        if provider.read_pointer() != current:
            raise ValueError('Pointer raced before floor update')
        if version(provider.read_floor()) > version(target.version):
            raise ValueError('Floor raced')
        provider.set_floor(target.version)
        if version(provider.read_floor()) != version(target.version):
            raise ValueError('Floor readback failed')
        desired = plan.pointer(target)
        if current != desired:
            provider.replace_pointer(current, desired)
        if provider.read_pointer() != desired:
            raise ValueError('Pointer readback failed')
        receipt = canonical({'transaction': plan.transaction, 'manifest': target.manifest,
                             'pointer_sha256': hashlib.sha256(desired).hexdigest(),
                             'floor': target.version, 'launched': False})
        provider.write_receipt(plan.transaction, receipt)
        return json.loads(receipt)
