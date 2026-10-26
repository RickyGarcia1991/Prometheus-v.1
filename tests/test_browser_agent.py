"""No browser process or public networking in these adapter boundary tests."""
import hashlib
import json
import threading
from pathlib import Path

import pytest

from prometheus_assistant import browser_agent as browser


@pytest.mark.parametrize('value', [
    'http://example.com/', 'https://127.0.0.1/', 'https://127.1/', 'https://0x7f000001/',
    'https://[::1]/', 'https://localhost/', 'https://a.local/', 'https://a.internal/',
    'https://a.test/', 'https://a.invalid/', 'https://example.com:8443/',
    'https://user@example.com/', 'https://user:pass@example.com/', 'https://example.com/#section',
    'https://example.com\\@evil.org/', 'https://example.com/\n', 'file:///C:/private',
    'https://example.com./', 'https://bad_.example.com/', 'https://example.com/' + 'x' * 2000,
    None, {}, 42,
])
def test_refuses_nonpublic_or_ambiguous_targets(value):
    with pytest.raises(ValueError):
        browser.canonical_arguments('read', {'url': value})


def test_idna_case_and_default_port_are_canonical():
    assert browser.canonical_arguments('read', {'url': 'https://BÜCHER.de:443/a?q=One'}) == {
        'url': 'https://xn--bcher-kva.de/a?q=One'}


@pytest.mark.parametrize('action,args', [('click', {'url': 'https://example.com'}),
    ('read', {'url': 'https://example.com', 'javascript': 'steal()'}), ('read', {}), ('read', [])])
def test_exact_action_and_arguments(action, args):
    with pytest.raises(ValueError): browser.canonical_arguments(action, args)


def message(**changes):
    return dict({'url': 'https://example.com/api', 'method': 'GET', 'resource_type': 'Fetch'}, **changes)


def counts():
    return {'requests': 0, 'fetched': 0, 'blocked': 0, 'bytes': 0}


@pytest.mark.parametrize('changes', [{'method': 'POST'}, {'url': 'https://other.example.com/api'},
    {'url': 'http://127.0.0.1/'}, {'resource_type': 'Image'}, {'resource_type': 'Media'}])
def test_disallowed_requests_never_reach_network(monkeypatch, changes):
    monkeypatch.setattr(browser, '_fetch', lambda *args: pytest.fail('Forbidden network request'))
    counter = counts()
    assert browser._response(message(**changes), 'example.com', browser._Deadline(None), counter) == {'ok': False}
    assert counter['blocked'] == 1


def test_total_download_budget_applies_to_next_fetch(monkeypatch):
    recorded = []
    monkeypatch.setattr(browser, '_fetch', lambda url, host, event, size: recorded.append(size) or b'{}')
    counter = counts(); counter['bytes'] = browser.MAX_TOTAL_BYTES - 7
    assert browser._response(message(), 'example.com', browser._Deadline(None), counter)['ok']
    assert recorded == [7]
    counter['bytes'] = browser.MAX_TOTAL_BYTES
    assert browser._response(message(), 'example.com', browser._Deadline(None), counter) == {'ok': False}
    assert recorded == [7]


def test_document_failure_is_not_a_successful_blank_page(monkeypatch):
    monkeypatch.setattr(browser, '_fetch', lambda *args: (_ for _ in ()).throw(RuntimeError('HTTP redirect refused')))
    with pytest.raises(ValueError, match='selected public page'):
        browser._response(message(resource_type='Document'), 'example.com', browser._Deadline(None), counts())


def test_script_navigation_requires_a_new_selected_read(monkeypatch):
    monkeypatch.setattr(browser, '_fetch', lambda *args: pytest.fail('Unselected navigation'))
    with pytest.raises(ValueError):
        browser._response(message(resource_type='Document'), 'example.com', browser._Deadline(None), counts(), 'https://example.com/')


def test_cancellation_during_fetch_propagates(monkeypatch):
    event = threading.Event()
    def fetch(*args):
        event.set(); raise RuntimeError('cancel')
    monkeypatch.setattr(browser, '_fetch', fetch)
    with pytest.raises(RuntimeError, match='cancelled'):
        browser._response(message(), 'example.com', browser._Deadline(event), counts())


def test_request_limit_stops_before_network(monkeypatch):
    monkeypatch.setattr(browser, '_fetch', lambda *args: pytest.fail('Over-budget fetch'))
    counter = counts(); counter['requests'] = browser.MAX_REQUESTS
    with pytest.raises(RuntimeError, match='request limit'):
        browser._response(message(), 'example.com', browser._Deadline(None), counter)


def bundle(tmp_path, monkeypatch):
    folder = tmp_path / browser.BUNDLE_RELATIVE
    folder.mkdir(parents=True)
    (folder / 'module.py').write_bytes(b'original')
    raw = json.dumps({'files': {'module.py': {'bytes': 8, 'sha256': hashlib.sha256(b'original').hexdigest()}}}).encode()
    (folder / 'manifest.json').write_bytes(raw)
    monkeypatch.setattr(browser, 'MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())
    return folder


@pytest.mark.parametrize('change', ['changed', 'missing', 'extra', 'manifest'])
def test_bundle_integrity_rejects_changes(tmp_path, monkeypatch, change):
    folder = bundle(tmp_path, monkeypatch)
    if change == 'changed': (folder / 'module.py').write_bytes(b'modified')
    if change == 'missing': (folder / 'module.py').unlink()
    if change == 'extra': (folder / 'surprise.pth').write_text('run stuff')
    if change == 'manifest': (folder / 'manifest.json').write_text('{}')
    with pytest.raises(ValueError): browser._verify_bundle(tmp_path)


def test_complete_bundle_and_precancel(tmp_path, monkeypatch):
    folder = bundle(tmp_path, monkeypatch)
    assert browser._verify_bundle(tmp_path) == folder
    event = threading.Event(); event.set()
    with pytest.raises(RuntimeError, match='cancelled'): browser._verify_bundle(tmp_path, event)


def test_status_does_not_hash_payload_or_claim_payload_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(browser.sys, 'platform', 'win32')
    bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(browser, '_verify_bundle', lambda *args: pytest.fail('Expensive status verification'))
    result = browser.status(tmp_path)
    assert result['provider'] == 'browser_use'
    assert result['installed'] is True
    assert result['agentic_tasks_qualified'] is False
    assert 'checked before each execution' in result['integrity']


def test_worker_environment_excludes_owner_profile_keys_and_python_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret')
    monkeypatch.setenv('HTTP_PROXY', 'http://secret/')
    monkeypatch.setenv('PYTHONPATH', 'owner-hooks')
    env = browser._environment(tmp_path)
    assert not {'OPENAI_API_KEY', 'HTTP_PROXY', 'PYTHONPATH'} & env.keys()
    assert env['USERPROFILE'] == str(tmp_path)
    assert env['ANONYMIZED_TELEMETRY'] == env['BROWSER_USE_CLOUD_SYNC'] == 'false'


def test_policy_checked_before_bundle_or_process(monkeypatch, tmp_path):
    from prometheus_assistant import application_policy
    def deny(capability):
        assert capability == 'integrations.browser_use'; raise ValueError('Denied')
    monkeypatch.setattr(application_policy, 'require_dispatch', deny)
    monkeypatch.setattr(browser, '_verify_bundle', lambda *args: pytest.fail('Bundle touched before policy'))
    with pytest.raises(ValueError, match='Denied'):
        browser.execute('read', {'url': 'https://example.com'}, data_root=tmp_path, resource_root=tmp_path)


def test_windows_profile_cleanup_waits_for_owned_handles(monkeypatch, tmp_path):
    attempts = []
    def remove(path):
        assert path == tmp_path
        attempts.append(path)
        if len(attempts) < 3: raise PermissionError('Owned child is still releasing cache')
    monkeypatch.setattr(browser.shutil, 'rmtree', remove)
    monkeypatch.setattr(browser.time, 'sleep', lambda _: None)
    browser._cleanup_job(tmp_path)
    assert len(attempts) == 3


def test_execute_returns_canonical_provider_identity_without_native_work(monkeypatch, tmp_path):
    monkeypatch.setattr(browser.sys, 'platform', 'win32')
    from types import SimpleNamespace
    from prometheus_assistant import application_policy, hardware
    monkeypatch.setattr(application_policy, 'require_dispatch', lambda capability: None)
    monkeypatch.setattr(hardware, 'detect_hardware', lambda: SimpleNamespace(available_ram_gib=2))
    monkeypatch.setattr(browser, '_verify_bundle', lambda *args: tmp_path)
    monkeypatch.setattr(browser, '_run_reader', lambda *args: {'text': 'fixture content'})
    # This contract test does not launch the Windows-only worker. Isolate its
    # host prerequisite, while separate tests keep the native gate fail-closed.
    monkeypatch.setattr(browser, '_require_windows_runtime', lambda: None)
    monkeypatch.setenv('SystemDrive', tmp_path.drive)
    monkeypatch.setattr(browser.tempfile, 'gettempdir', lambda: str(tmp_path))
    result = browser.execute('read', {'url': 'https://example.com'},
                             data_root=tmp_path/'unused-portable-data', resource_root=tmp_path)
    assert result['provider'] == 'browser_use'
    assert result['result'] == {'text': 'fixture content'}
    assert result['generated_answer'] is False
    assert not (tmp_path/'unused-portable-data').exists()


def test_unsupported_native_runtime_is_rejected_before_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(browser.sys, 'platform', 'darwin')
    from types import SimpleNamespace
    from prometheus_assistant import application_policy
    monkeypatch.setattr(application_policy, 'require_dispatch', lambda capability: None)
    monkeypatch.setattr(browser, 'os', SimpleNamespace(name='posix'))
    def unexpected(*args):
        pytest.fail('An unsupported host must not inspect or execute the native bundle')
    monkeypatch.setattr(browser, '_verify_bundle', unexpected)
    monkeypatch.setattr(browser, '_run_reader', unexpected)
    with pytest.raises(ValueError, match='Windows Python 3.12'):
        browser.execute('read', {'url': 'https://example.com'}, data_root=tmp_path, resource_root=tmp_path)
