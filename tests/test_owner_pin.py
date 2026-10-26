"""Backup-PIN tests use temporary owners and synthetic test passkeys only."""
import json
import sqlite3
import threading
from pathlib import Path
import pytest
from test_owner_access import Authenticator, gate, enroll, ORIGIN
from prometheus_assistant.owner_access import OwnerAccess


def configured(tmp_path):
    app = gate(tmp_path)
    device = Authenticator()
    enroll(app, device)
    begin = app.begin('authenticate')
    _, cookie = app.complete(begin['request_id'], device.reply(begin, counter=2))
    app.configure_pin('6392', '6392', cookie)
    return app, device, cookie


def test_no_pin_exists_until_authenticated_owner_chooses_it(tmp_path):
    app = gate(tmp_path)
    with pytest.raises(ValueError): app.configure_pin('6392', '6392')
    with pytest.raises(ValueError): app.unlock_pin('6392')
    assert not app.pin.path.exists()
    device = Authenticator(); enroll(app, device)
    with pytest.raises(ValueError): app.configure_pin('6392', '6392')
    assert not app.pin.path.exists()


@pytest.mark.parametrize('pin,confirmation', [('1234','1234'),('1111','1111'),('6392','6393'),('639','639'),('63920','63920'),('٦٣٩٢','٦٣٩٢'),('6392','٦٣٩٢'),(6392,6392)])
def test_invalid_setup_cannot_replace_pin(tmp_path, pin, confirmation):
    app, _, cookie = configured(tmp_path)
    with pytest.raises(ValueError): app.configure_pin(pin, confirmation, cookie)
    assert app.unlock_pin('6392')[0]['unlocked']


def test_pin_is_hashed_and_leading_zero_is_preserved(tmp_path):
    app, _, cookie = configured(tmp_path)
    app.configure_pin('0649', '0649', cookie)
    with sqlite3.connect(app.pin.path) as db:
        salt, verifier = db.execute('SELECT salt,verifier FROM pin').fetchone()
    assert len(salt) == len(verifier) == 32
    assert verifier != b'0649' and b'0649' not in app.pin.path.read_bytes()
    assert app.unlock_pin('0649')[0]['unlocked']


def test_wrong_pin_never_creates_session_and_backoff_is_enforced(tmp_path):
    app, _, _ = configured(tmp_path)
    before = dict(app.sessions)
    with pytest.raises(ValueError, match='Incorrect'): app.unlock_pin('6393')
    assert app.sessions == before
    assert app.state()['pin']['remaining'] == 4
    with pytest.raises(ValueError, match='Wait'): app.unlock_pin('6392')
    assert app.state()['pin']['remaining'] == 4


def test_five_failures_persist_after_restart_and_passkey_recovers(tmp_path):
    app, device, _ = configured(tmp_path)
    now = [1000.0]; app.pin.clock = lambda: now[0]
    for i in range(5):
        with pytest.raises(ValueError): app.unlock_pin('6393')
        now[0] += 60
    assert app.state()['pin']['locked']
    restarted = OwnerAccess(app.root, ORIGIN)
    with pytest.raises(ValueError, match='locked'): restarted.unlock_pin('6392')
    begin = restarted.begin('authenticate')
    _, cookie = restarted.complete(begin['request_id'], device.reply(begin, counter=3))
    assert restarted.passkey_authenticated(cookie)
    assert not restarted.state()['pin']['locked']
    assert restarted.unlock_pin('6392')[0]['unlocked']


def test_pin_session_cannot_change_recovery_or_add_passkeys(tmp_path):
    app, _, owner = configured(tmp_path)
    _, pin_cookie = app.unlock_pin('6392')
    assert app.authenticated(pin_cookie) and not app.passkey_authenticated(pin_cookie)
    with app.grant_owner(pin_cookie) as binding: assert binding
    with pytest.raises(ValueError): app.configure_pin('0649','0649',pin_cookie)
    with pytest.raises(ValueError): app.disable_pin(pin_cookie)
    with pytest.raises(ValueError): app.begin('register',cookie=pin_cookie)
    app.disable_pin(owner)
    assert not app.authenticated(pin_cookie)
    assert app.passkey_authenticated(owner)
    with pytest.raises(ValueError): app.unlock_pin('6392')


def test_change_revokes_existing_pin_sessions(tmp_path):
    app, _, owner = configured(tmp_path)
    _, pin_cookie = app.unlock_pin('6392')
    app.configure_pin('0649','0649',owner)
    assert not app.authenticated(pin_cookie)
    assert app.unlock_pin('0649')[0]['unlocked']
    app.logout(owner)
    assert not app.passkey_authenticated(owner)


def test_pin_survives_restart_but_does_not_restore_sessions(tmp_path):
    app, _, _ = configured(tmp_path)
    _, cookie = app.unlock_pin('6392')
    restarted = OwnerAccess(app.root, ORIGIN)
    assert not restarted.authenticated(cookie)
    assert restarted.unlock_pin('6392')[0]['unlocked']


def test_adding_genuine_backup_passkey_preserves_pin(tmp_path):
    app, _, owner = configured(tmp_path)
    other = Authenticator()
    begin = app.begin('register',cookie=owner)
    app.complete(begin['request_id'], other.reply(begin,register=True), cookie=owner)
    assert app.unlock_pin('6392')[0]['unlocked']


def test_supported_backup_excludes_pin_verifier(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    from prometheus_assistant.maintenance import create_backup, restore_backup
    app, _, _ = configured(tmp_path)
    with MemoryStore(tmp_path/'memory.sqlite3') as memory: memory.create_session('test')
    saved = create_backup(tmp_path/'memory.sqlite3',tmp_path/'backups',require_separate_volume=False)
    manifest = json.loads((Path(saved['path'])/'manifest.json').read_text())
    assert not any('/secrets/' in row['path'] or 'backup-pin' in row['path'] for row in manifest['files'])
    restore_backup(saved['path'],tmp_path/'restored')
    restored = OwnerAccess(tmp_path/'restored/OwnerAccess', ORIGIN)
    assert restored.state()['enrolled'] and not restored.state()['pin']['configured']


def test_corrupt_pin_storage_does_not_unlock_or_break_passkey_recovery(tmp_path):
    app, device, _ = configured(tmp_path)
    app.pin.path.write_bytes(b'not a sqlite database')
    assert app.state()['pin']['unavailable']
    with pytest.raises(sqlite3.Error): app.unlock_pin('6392')
    begin = app.begin('authenticate')
    _, owner = app.complete(begin['request_id'],device.reply(begin,counter=3))
    assert app.passkey_authenticated(owner)


def test_http_pin_flow_and_cross_origin_rejection(tmp_path):
    from prometheus_assistant.ui_server import Interface, Server
    from urllib.request import Request, build_opener, ProxyHandler
    from urllib.error import HTTPError
    gate(tmp_path)
    app = Interface(tmp_path/'memory.sqlite3')
    server = Server(('127.0.0.1',0),app)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    client = build_opener(ProxyHandler({}))
    def request(path, body=None, cookie=None, origin=None, token=None):
        headers = {'Origin':origin or server.owner_origin,'Host':server.owner_origin.removeprefix('http://'),
                   'Content-Type':'application/json','X-Prometheus-Token':app.token if token is None else token}
        if cookie: headers['Cookie']='prometheus_owner='+cookie
        req = Request(server.origin+path,None if body is None else json.dumps(body).encode(),headers)
        try:
            with client.open(req,timeout=3) as response:
                return response.status,json.loads(response.read()),response.headers.get('Set-Cookie','')
        except HTTPError as error: return error.code,json.loads(error.read()),''
    try:
        access=server.owner_access; device=Authenticator()
        begin=access.begin('register',setup_ticket='private-test-ticket')
        access.complete(begin['request_id'],device.reply(begin,register=True,origin=server.owner_origin))
        begin=access.begin('authenticate')
        _, owner=access.complete(begin['request_id'],device.reply(begin,counter=2,origin=server.owner_origin))
        body={'pin':'6392','confirmation':'6392'}
        assert request('/api/owner/pin/setup',body)[0]==400
        assert request('/api/owner/pin/setup',body,owner,origin='https://evil.example')[0] in (400,403)
        assert request('/api/owner/pin/setup',body,owner,token='wrong')[0]==403
        assert not access.state()['pin']['configured']
        assert request('/api/owner/pin/setup',body,owner)[0]==200
        assert request('/api/status')[0]==401
        assert request('/api/owner/pin/unlock',{'pin':'6392','unexpected':True})[0]==400
        code, result, header=request('/api/owner/pin/unlock',{'pin':'6392'})
        assert code==200 and result['unlocked'] and 'HttpOnly' in header and 'SameSite=Strict' in header
        pin_cookie=header.split(';')[0].split('=',1)[1]
        assert request('/api/status',cookie=pin_cookie)[0]==200
        assert request('/api/owner/pin/setup',body,pin_cookie)[0]==400
        assert request('/api/owner/pin/disable',{},pin_cookie)[0]==400
        assert request('/api/owner/begin',{'kind':'register'},pin_cookie)[0]==400
        assert request('/api/owner/pin/disable',{},owner)[0]==200
        assert request('/api/status',cookie=pin_cookie)[0]==401
    finally:
        server.shutdown();server.server_close();thread.join(2);app.close()
