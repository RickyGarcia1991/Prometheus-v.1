"""Real cryptographic WebAuthn verification with a synthetic authenticator."""
import hashlib
import json
import secrets
import pytest

pytest.importorskip('fido2')
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2.cose import ES256
from fido2.utils import websafe_encode
from fido2.webauthn import AttestedCredentialData, AttestationObject, AuthenticatorData, CollectedClientData
from prometheus_assistant.owner_access import OwnerAccess

ORIGIN = 'http://localhost:54555'


def gate(tmp_path):
    root = tmp_path/'OwnerAccess'; root.mkdir()
    (root/'required.json').write_text(json.dumps({'setup_ticket_sha256':hashlib.sha256(b'private-test-ticket').hexdigest()}))
    app = OwnerAccess(root, ORIGIN)
    assert app.ready
    return app


class Authenticator:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        self.data = AttestedCredentialData.create(bytes(16), self.id, ES256.from_cryptography_key(self.key.public_key()))

    def reply(self, begin, *, register=False, origin=ORIGIN, rp='localhost', flags=5, counter=1, wrong_key=False):
        client = CollectedClientData.create('webauthn.create' if register else 'webauthn.get', begin['options']['publicKey']['challenge'], origin)
        auth = AuthenticatorData.create(hashlib.sha256(rp.encode()).digest(), flags | (64 if register else 0), counter, self.data if register else b'')
        response = {'clientDataJSON':websafe_encode(client)}
        if register:
            response['attestationObject'] = websafe_encode(AttestationObject.create('none',auth,{}))
        else:
            key = ec.generate_private_key(ec.SECP256R1()) if wrong_key else self.key
            response.update(authenticatorData=websafe_encode(auth), signature=websafe_encode(key.sign(auth+client.hash,ec.ECDSA(hashes.SHA256()))))
        return {'id':websafe_encode(self.id),'rawId':websafe_encode(self.id),'type':'public-key','response':response}


def enroll(app, device):
    begin = app.begin('register', setup_ticket='private-test-ticket')
    result, cookie = app.complete(begin['request_id'], device.reply(begin, register=True))
    assert result['registered'] and cookie is None


def test_real_signature_unlocks_but_registration_alone_does_not(tmp_path):
    app=gate(tmp_path); device=Authenticator(); enroll(app,device)
    assert not app.authenticated(None)
    begin=app.begin('authenticate')
    result,cookie=app.complete(begin['request_id'],device.reply(begin,counter=2))
    assert result['unlocked'] and app.authenticated(cookie)
    app.logout(cookie)
    assert not app.authenticated(cookie)
    assert OwnerAccess(app.root,ORIGIN).state()['enrolled']
    assert not OwnerAccess(app.root,ORIGIN).authenticated(cookie)


@pytest.mark.parametrize('change',[{'origin':'https://evil.example'},{'rp':'evil.example'},{'flags':1},{'flags':4},{'wrong_key':True},{'counter':1}])
def test_invalid_authentication_stays_locked_and_challenge_is_consumed(tmp_path,change):
    app=gate(tmp_path);device=Authenticator();enroll(app,device)
    begin=app.begin('authenticate')
    with pytest.raises(ValueError):app.complete(begin['request_id'],device.reply(begin,**change))
    with pytest.raises(ValueError):app.complete(begin['request_id'],device.reply(begin,counter=3))
    assert not app.sessions


def test_setup_and_additional_registration_require_authorization(tmp_path):
    app=gate(tmp_path)
    with pytest.raises(ValueError):app.begin('register',setup_ticket='wrong')
    device=Authenticator();enroll(app,device)
    with pytest.raises(ValueError):app.begin('register',setup_ticket='private-test-ticket')


def test_expiry_cookie_binding_and_replay(tmp_path):
    app=gate(tmp_path);device=Authenticator();enroll(app,device)
    begin=app.begin('authenticate',cookie='one')
    with pytest.raises(ValueError):app.complete(begin['request_id'],device.reply(begin,counter=2),cookie='other')
    begin=app.begin('authenticate')
    app.clock=lambda:10**12
    with pytest.raises(ValueError):app.complete(begin['request_id'],device.reply(begin,counter=2))


def test_empty_configuration_stays_locked_when_setup_ticket_is_missing(tmp_path):
    root=tmp_path/'OwnerAccess';root.mkdir();(root/'required.json').write_text('{}')
    app=OwnerAccess(root,ORIGIN)
    with pytest.raises(ValueError):app.begin('register')
    assert app.state()['required'] and not app.authenticated(None)


def test_public_passkeys_survive_supported_backup_and_session_expires(tmp_path):
    from prometheus_assistant.memory import MemoryStore
    from prometheus_assistant.maintenance import create_backup, restore_backup
    app=gate(tmp_path);device=Authenticator();enroll(app,device)
    begin=app.begin('authenticate');_,cookie=app.complete(begin['request_id'],device.reply(begin,counter=2))
    with MemoryStore(tmp_path/'memory.sqlite3') as memory:memory.create_session('test')
    backup=create_backup(tmp_path/'memory.sqlite3',tmp_path/'backups',require_separate_volume=False)
    restored=restore_backup(backup['path'],tmp_path/'restored')
    restored_gate=OwnerAccess(tmp_path/'restored/OwnerAccess',ORIGIN)
    assert restored_gate.state()['enrolled'] and not restored_gate.authenticated(cookie)
    app.clock=lambda:10**12
    assert not app.authenticated(cookie)


def test_http_gate_protects_history_jobs_status_and_actions(tmp_path):
    from prometheus_assistant.ui_server import Interface, Server
    from prometheus_assistant.memory import MemoryStore
    from urllib.request import Request, build_opener, ProxyHandler
    from urllib.error import HTTPError
    import threading
    seeded=gate(tmp_path)
    app=Interface(tmp_path/'memory.sqlite3')
    with MemoryStore(app.memory_path) as memory:
        sid=memory.create_session('test');memory.save_exchange(sid,'Private question','Private answer')
    server=Server(('127.0.0.1',0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=build_opener(ProxyHandler({}))
    def request(path,body=None,cookie=None):
        headers={'Origin':server.owner_origin,'Host':server.owner_origin.removeprefix('http://'),
                 'Content-Type':'application/json','X-Prometheus-Token':app.token}
        if cookie:headers['Cookie']='prometheus_owner='+cookie
        r=Request(server.origin+path,None if body is None else json.dumps(body).encode(),headers)
        try:
            with client.open(r,timeout=3) as response:return response.status,response.read()
        except HTTPError as error:return error.code,error.read()
    try:
        for path in ['/api/sessions','/api/history/'+sid,'/api/status','/api/job/not-real','/api/remote/state']:
            code,body=request(path);assert code==401 and b'Private' not in body
        assert request('/api/jobs',{'mode':'chat','prompt':'Who am I?'})[0]==401
        assert request('/api/remote/action',{})[0]==401
        assert request('/api/close',{})[0]==401
        assert request('/api/speech',{'text':'Private answer'})[0]==401
        assert b'Unlock Prometheus' in request('/')[1]
        device=Authenticator();access=server.owner_access
        begin=access.begin('register',setup_ticket='private-test-ticket')
        access.complete(begin['request_id'],device.reply(begin,register=True,origin=server.owner_origin))
        begin=access.begin('authenticate')
        _,cookie=access.complete(begin['request_id'],device.reply(begin,counter=2,origin=server.owner_origin))
        assert b'Private answer' in request('/api/history/'+sid,cookie=cookie)[1]
        assert request('/api/owner/lock',{},cookie=cookie)[0]==200
        assert request('/api/history/'+sid,cookie=cookie)[0]==401
    finally:
        server.shutdown();server.server_close();thread.join(2);app.close()
