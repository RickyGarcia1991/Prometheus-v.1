import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

PATH = Path(__file__).parents[1] / 'tools/Prometheus.HostAgent/controller_pointer_transaction.py'
spec = importlib.util.spec_from_file_location('pointer_tx', PATH)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
KEY = b'x' * 32

class Provider:
    def __init__(self, plan, fault=None):
        self.pointer = plan.previous
        self.floor = '1.0.10'
        self.intent = None
        self.receipt = None
        self.fault = fault
        self.events = []
    def hit(self, event):
        self.events.append(event)
        if event == self.fault:
            self.fault = None
            raise RuntimeError(event)
    def exclusive(self): return contextlib.nullcontext()
    def assert_context(self, host, volume):
        assert (host, volume) == ('host', 'volume')
        self.hit('context')
    def verify(self, package): self.hit('verify-' + package.version)
    def read_pointer(self): return self.pointer
    def read_floor(self): return self.floor
    def read_intent(self, tx): return self.intent
    def write_intent(self, tx, data):
        self.hit('before-intent'); self.intent = data; self.hit('after-intent')
    def stage(self, package): self.hit('stage-' + package.version)
    def set_floor(self, value):
        self.hit('before-floor')
        assert m.version(value) >= m.version(self.floor)
        self.floor = value; self.hit('after-floor')
    def replace_pointer(self, expected, desired):
        self.hit('before-pointer')
        assert self.pointer == expected
        self.pointer = desired; self.hit('after-pointer')
    def write_receipt(self, tx, data):
        self.hit('before-receipt'); self.receipt = data; self.hit('after-receipt')

@pytest.fixture
def plan():
    return m.Plan('a'*32, 'host', 'volume', b'{"controller_release":"Prometheus-Controller-v1.0.10","core":{"version":55},"unknown":[1,2]}',
                  m.Package('Prometheus-Controller-v1.0.11', 'b'*64), m.Package('Prometheus-Controller-v1.0.12', 'c'*64))

@pytest.mark.parametrize('fault', ['before-intent','after-intent','stage-1.0.12','stage-1.0.11','before-floor','after-floor','before-pointer','after-pointer','before-receipt','after-receipt'])
def test_resume_every_mutation_boundary(plan, fault):
    p = Provider(plan, fault)
    with pytest.raises(RuntimeError): m.execute(p, plan, KEY)
    # Durable provider state is retained; a new invocation reconstructs authority.
    result = m.execute(p, plan, KEY)
    assert result['floor'] == '1.0.11' and result['launched'] is False
    assert json.loads(p.pointer)['core'] == {'version': 55}
    assert json.loads(p.pointer)['unknown'] == [1,2]
    assert p.events.index('after-intent') < p.events.index('before-floor')
    previous = p.pointer
    m.execute(p, plan, KEY)
    assert previous == p.pointer

@pytest.mark.parametrize('change', ['intent','pointer','key','volume','manifest'])
def test_tampering_refused_without_floor_change(plan, change):
    p = Provider(plan, 'after-intent')
    with pytest.raises(RuntimeError): m.execute(p, plan, KEY)
    key = KEY
    if change == 'intent': p.intent += b' '
    if change == 'pointer': p.pointer += b' '
    if change == 'key': key = b'y'*32
    if change == 'volume': plan = m.Plan(plan.transaction, plan.host, 'other', plan.previous, plan.candidate, plan.recovery)
    if change == 'manifest': plan = m.Plan(plan.transaction, plan.host, plan.volume, plan.previous, m.Package(plan.candidate.release, 'd'*64), plan.recovery)
    with pytest.raises((ValueError, AssertionError)): m.execute(p, plan, key)
    assert p.floor == '1.0.10'


def test_explicit_forward_recovery_never_downgrades(plan):
    p=Provider(plan)
    m.execute(p, plan, KEY)
    m.execute(p, plan, KEY, forward_recovery=True)
    assert p.floor == '1.0.12'
    m.execute(p, plan, KEY)
    assert p.floor == '1.0.12' and p.pointer == plan.pointer(plan.recovery)


def test_higher_external_floor_refuses_candidate(plan):
    p=Provider(plan); p.floor='1.0.12'
    with pytest.raises(ValueError): m.execute(p,plan,KEY)
    assert p.pointer == plan.previous
    m.execute(p,plan,KEY,forward_recovery=True)
    assert p.floor == '1.0.12'


def test_duplicate_metadata_rejected_before_provider(plan):
    invalid=m.Plan(plan.transaction,plan.host,plan.volume,b'{"controller_release":"x","controller_release":"y"}',plan.candidate,plan.recovery)
    p=Provider(plan)
    with pytest.raises(ValueError): m.execute(p,invalid,KEY)
    assert not p.events

@pytest.mark.parametrize('raw', [b'NaN', b'Infinity', b'-Infinity', b'1e999', b'"\\ud800"'])
def test_non_json_nested_metadata_refused_before_writes(plan, raw):
    previous=b'{"controller_release":"old","nested":[{"value":'+raw+b'}]}'
    invalid=m.Plan(plan.transaction,plan.host,plan.volume,previous,plan.candidate,plan.recovery)
    p=Provider(plan)
    with pytest.raises((ValueError,UnicodeError)):
        m.execute(p,invalid,KEY)
    assert not p.events

@pytest.mark.parametrize('kind',['same-version','same-package','same-manifest','lower-version'])
def test_forward_recovery_must_be_strictly_newer_distinct(plan,kind):
    recovery=m.Package('Prometheus-Controller-v1.0.11','c'*64)
    if kind=='same-package': recovery=plan.candidate
    if kind=='same-manifest': recovery=m.Package('Prometheus-Controller-v1.0.12',plan.candidate.manifest)
    if kind=='lower-version': recovery=m.Package('Prometheus-Controller-v1.0.10','c'*64)
    invalid=m.Plan(plan.transaction,plan.host,plan.volume,plan.previous,plan.candidate,recovery)
    p=Provider(plan)
    with pytest.raises(ValueError): m.execute(p,invalid,KEY)
    assert not p.events
