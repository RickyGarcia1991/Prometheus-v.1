import json
import hashlib
KEY = "synthetic-private-control-key-0000"
import urllib.request
import pytest
from prometheus_assistant import smart_home as home


@pytest.fixture
def config(tmp_path):
    data = {'enabled': True, 'control_key_sha256': hashlib.sha256(KEY.encode()).hexdigest(), 'endpoint': 'http://127.0.0.1:8123', 'allow_insecure_http': True,
            'devices': {'bedroom lights': {'entity_id': 'light.bedroom', 'actions': ['on', 'off', 'brightness', 'color', 'status']},
                        'porch': {'entity_id': 'switch.porch', 'actions': ['on', 'off']},
                        'speaker': {'entity_id': 'media_player.room', 'actions': ['pause', 'volume']},
                        'echo announcement': {'entity_id': 'notify.echo', 'actions': ['announce']},
                        'thermostat': {'entity_id': 'climate.room', 'actions': ['temperature'], 'minimum': 16, 'maximum': 27, 'unit': 'C'}}}
    (tmp_path/'config.json').write_text(json.dumps(data))
    return tmp_path, data


class FakeHub:
    def __init__(self, *args): self.calls = []
    def request(self, path, body=None):
        self.calls.append((path, body))
        return {'state': 'off', 'attributes': {'temperature_unit': '°C'}}


def test_standby_never_requests_credentials_or_network(tmp_path):
    def fail(*args): raise AssertionError('must not access pairing')
    assert home.handle_command('turn off bedroom lights', tmp_path, control_key=KEY, client_factory=fail, token_reader=fail)['standby']


def test_commands_use_services_not_state_mutation(config):
    root, data = config
    hub = FakeHub()
    answer = home.handle_command('turn off the bedroom lights; turn on porch', root,
        control_key=KEY, client_factory=lambda *args: hub, token_reader=lambda config: 'synthetic-test-token')
    assert hub.calls == [('/api/services/light/turn_off', {'entity_id': 'light.bedroom'}),
        ('/api/states/light.bedroom', None), ('/api/services/switch/turn_on', {'entity_id': 'switch.porch'}),
        ('/api/states/switch.porch', None)]
    assert 'hub reports off' in answer['reply']


@pytest.mark.parametrize('prompt', ['turn off bedroom', 'unlock porch', 'turn on all',
    'turn off bedroom lights; unlock door', 'set bedroom lights brightness to 101%',
    'set bedroom lights color to ultraviolet', 'announce on echo announcement: <audio src="x"/>',
    'set thermostat temperature to 40'])
def test_invalid_batches_have_no_side_effects(config, prompt):
    root, _ = config
    def fail(*args): raise AssertionError('must validate before credentials/network')
    result = home.handle_command(prompt, root, control_key=KEY, client_factory=fail, token_reader=fail)
    assert result['connected'] is False


@pytest.mark.parametrize('url', ['http://example.com', 'http://169.254.169.254',
    'http://8.8.8.8', 'https://user:secret@example.com', 'https://example.com/api',
    'https://example.com/?token=x', 'file:///tmp/test'])
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(home.HomeError): home.validate_endpoint(url, True, True)


def test_no_redirect_with_credentials():
    req = urllib.request.Request('https://example.com/api/', headers={'Authorization': 'Bearer synthetic'})
    with pytest.raises(home.HomeError): home.NoRedirect().redirect_request(req, None, 302, 'Found', {}, 'https://other.test')


def test_brightness_and_volume_payloads(config):
    _, data = config
    assert home.parse_command('dim bedroom lights to 40%', data['devices']).data == {'entity_id': 'light.bedroom', 'brightness_pct': 40}
    assert home.parse_command('set speaker volume to 25%', data['devices']).data == {'entity_id': 'media_player.room', 'volume_level': .25}


def test_drop_in_is_explicit_handoff_without_network(config):
    root, _ = config
    def fail(*args): raise AssertionError('no intercom action')
    assert 'Alexa app' in home.handle_command('drop in on kitchen', root, control_key=KEY, client_factory=fail, token_reader=fail)['reply']


def test_failure_stops_batch_without_retries(config):
    root, _ = config
    hub = FakeHub()
    def uncertain(path, body=None):
        hub.calls.append((path, body))
        raise home.HomeError('Uncertain outcome')
    hub.request = uncertain
    result = home.handle_command('turn off bedroom lights; turn on porch', root, control_key=KEY, client_factory=lambda *args: hub, token_reader=lambda config: 'synthetic')
    assert len(hub.calls) == 1
    assert 'Remaining commands were not sent' in result['reply']


def test_thermostat_unit_mismatch_never_posts(config):
    root, _ = config
    hub = FakeHub()
    def different_unit(path, body=None):
        hub.calls.append((path, body))
        return {'state': 'heat', 'attributes': {'temperature_unit': '°F'}}
    hub.request = different_unit
    result = home.handle_command('set thermostat temperature to 22', root, control_key=KEY, client_factory=lambda *args: hub, token_reader=lambda config: 'synthetic')
    assert len(hub.calls) == 1 and hub.calls[0][1] is None
    assert 'unit could not be confirmed' in result['reply']


@pytest.mark.parametrize('entry', [{'entity_id': 'lock.front', 'actions': ['on']},
    {'entity_id': 'light.all', 'actions': ['delete']}, {'entity_id': '../admin', 'actions': ['on']}])
def test_unsupported_configuration_is_rejected(config, entry):
    root, data = config
    data['devices']['bad'] = entry
    (root/'config.json').write_text(json.dumps(data))
    with pytest.raises(home.HomeError): home.load_config(root)


def test_bootstrap_alone_does_not_authorize_home(config):
    root, _ = config
    def fail(*args): raise AssertionError('must not access credentials or network')
    result = home.handle_command('turn off bedroom lights', root, client_factory=fail, token_reader=fail)
    assert 'private smart-home control key' in result['reply']

def test_credential_cannot_move_between_endpoints_or_pairings(config):
    _, data = config
    record = {'endpoint': data['endpoint'], 'control_key_sha256': data['control_key_sha256'], 'token': 'synthetic'}
    assert home.validate_credential_record(record, data) == 'synthetic'
    for change in [{'endpoint': 'https://different.example'}, {'control_key_sha256': '0'*64}]:
        with pytest.raises(home.HomeError): home.validate_credential_record({**record, **change}, data)


def test_scene_status_actually_reads_hub(config):
    root, data = config
    data['devices']['evening'] = {'entity_id': 'scene.evening', 'actions': ['activate', 'status']}
    (root/'config.json').write_text(json.dumps(data))
    hub = FakeHub()
    home.handle_command('status evening', root, control_key=KEY, client_factory=lambda *args: hub, token_reader=lambda config: 'synthetic')
    assert hub.calls == [('/api/states/scene.evening', None)]


def test_later_thermostat_preflight_preserves_prior_effects(config):
    root, _ = config
    hub = FakeHub()
    def response(path, body=None):
        hub.calls.append((path, body))
        if path == '/api/config': return {'unit_system': {'temperature': '°F'}}
        return {'state': 'off'}
    hub.request = response
    result = home.handle_command('turn off bedroom lights; set thermostat temperature to 22', root, control_key=KEY, client_factory=lambda *args: hub, token_reader=lambda config: 'synthetic')
    assert 'bedroom lights: hub reports off' in result['reply']
    assert 'No temperature command was sent' in result['reply']
    assert not any('set_temperature' in call[0] for call in hub.calls)

def test_real_loopback_rest_contract(config):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    root, data = config
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            observed.append((self.path, body, self.headers.get('Authorization')))
            self.send_response(200); self.end_headers(); self.wfile.write(b'[]')
        def do_GET(self):
            observed.append((self.path, None, self.headers.get('Authorization')))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"state":"off"}')
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            data['endpoint'] = f'http://127.0.0.1:{server.server_port}'
            (root/'config.json').write_text(json.dumps(data))
            result = home.handle_command('turn off bedroom lights', root, control_key=KEY, token_reader=lambda config: 'synthetic-hub-token')
            assert 'hub reports off' in result['reply']
            assert observed == [('/api/services/light/turn_off', {'entity_id':'light.bedroom'}, 'Bearer synthetic-hub-token'),
                                ('/api/states/light.bedroom', None, 'Bearer synthetic-hub-token')]
            assert 'synthetic-hub-token' not in json.dumps(result)
        finally:
            server.shutdown(); thread.join()
