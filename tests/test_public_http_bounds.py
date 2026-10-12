"""Exercise the actual HTTP parser against a controlled transport boundary."""
import io
import socket
import threading
import time

import pytest

from prometheus_assistant import public_http as http


URL = 'https://example.org/resource'
HOSTS = {'example.org'}
PUBLIC_IP = '93.184.216.34'


class Input(io.RawIOBase):
    def __init__(self, transport):
        super().__init__()
        self.transport = transport

    def readable(self):
        return True

    def readinto(self, target):
        transport = self.transport
        with transport.condition:
            deadline = time.monotonic() + transport.timeout
            while not transport.buffer and not transport.eof:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('controlled socket read timeout')
                transport.condition.wait(remaining)
            count = min(len(target), len(transport.buffer))
            target[:count] = transport.buffer[:count]
            del transport.buffer[:count]
            return count


class Transport:
    """Only network I/O is replaced; HTTP framing and deadlines stay real."""
    def __init__(self):
        self.condition = threading.Condition()
        self.buffer = bytearray()
        self.eof = False
        self.timeout = 20
        self.requested = threading.Event()
        self.closed = threading.Event()
        self.request = bytearray()
        self.address = None
        self.server_hostname = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        self.address = address

    def do_handshake(self):
        pass

    def sendall(self, data):
        self.request.extend(data)
        self.requested.set()

    def makefile(self, mode):
        return io.BufferedReader(Input(self))

    def shutdown(self, how):
        self.finish()

    def close(self):
        self.closed.set()

    def feed(self, data):
        with self.condition:
            if not self.eof:
                self.buffer.extend(data)
            self.condition.notify_all()

    def finish(self):
        with self.condition:
            self.eof = True
            self.condition.notify_all()


@pytest.fixture
def network(monkeypatch):
    transport = Transport()
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_IP, 443))])
    monkeypatch.setattr(http.socket, 'socket', lambda *a, **k: transport)

    class TLSContext:
        def wrap_socket(self, raw, server_hostname, do_handshake_on_connect):
            transport.server_hostname = server_hostname
            return raw

    monkeypatch.setattr(http.ssl, 'create_default_context', TLSContext)
    # Covers the original transport too when demonstrating the framing defect.
    monkeypatch.setattr(http.socket, 'create_connection',
                        lambda address, timeout: transport)
    yield transport
    transport.finish()


def response(transport, body, headers=b'', status=b'200 OK'):
    transport.feed(b'HTTP/1.1 ' + status + b'\r\n' + headers + b'\r\n' + body)
    transport.finish()


def test_incomplete_content_length_is_rejected(network):
    response(network, b'abc', b'Content-Length: 5\r\n')
    with pytest.raises(RuntimeError, match='incomplete|truncated'):
        http.fetch_public_bytes(URL, HOSTS)
    assert network.closed.is_set()


def test_complete_payload_is_pinned_and_uses_identity_encoding(network):
    response(network, b'abc', b'Content-Length: 3\r\n')
    assert http.fetch_public_bytes(URL, HOSTS, overall_timeout=1) == b'abc'
    assert network.address == (PUBLIC_IP, 443)
    assert network.server_hostname == 'example.org'
    assert b'Accept-Encoding: identity\r\n' in network.request
    assert network.closed.is_set()


def test_none_socket_timeout_still_has_an_overall_bound(network):
    response(network, b'abc', b'Content-Length: 3\r\n')
    assert http.fetch_public_bytes(URL, HOSTS, timeout=None, overall_timeout=1) == b'abc'


def test_json_post_uses_same_pinned_transport_and_bounded_body(network):
    response(network, b'{}', b'Content-Length: 2\r\n')
    assert http.post_public_json(URL, HOSTS, {'query': 'portable tools'},
                                bearer_token='fc-test-only', overall_timeout=1) == b'{}'
    assert network.address == (PUBLIC_IP, 443) and network.server_hostname == 'example.org'
    assert network.request.startswith(b'POST /resource HTTP/1.1\r\n')
    assert b'Authorization: Bearer fc-test-only\r\n' in network.request
    assert b'Content-Type: application/json\r\n' in network.request
    assert network.request.endswith(b'{"query": "portable tools"}')


@pytest.mark.parametrize('key', ['x\r\nInjected: yes', 'x y', 'x' * 513])
def test_post_rejects_bad_credentials_before_network(network, key):
    with pytest.raises(ValueError, match='key format'):
        http.post_public_json(URL, HOSTS, {}, bearer_token=key)
    assert not network.requested.is_set()


def test_post_refuses_redirect_without_resending_credentials(network):
    response(network, b'', b'Location: https://other.example/path\r\n', b'302 Found')
    with pytest.raises(RuntimeError, match='redirect'):
        http.post_public_json(URL, HOSTS, {}, bearer_token='fc-test-only')
    assert network.request.count(b'POST /resource') == 1 and network.closed.is_set()


def test_post_http_error_does_not_expose_provider_body(network):
    response(network, b'secret provider details', b'', b'429 Too Many Requests')
    with pytest.raises(http.PublicHTTPError) as error:
        http.post_public_json(URL, HOSTS, {})
    assert error.value.status == 429 and 'secret' not in str(error.value)


def test_search_with_cancel_event_allows_service_response_time(network):
    def provider():
        assert network.requested.wait(1)
        time.sleep(2.15)
        response(network, b'{}', b'Content-Length: 2\r\n')
    worker = threading.Thread(target=provider)
    worker.start()
    try:
        assert http.post_public_json(URL, HOSTS, {'query': 'documents'},
            cancel_event=threading.Event(), timeout=3, overall_timeout=4) == b'{}'
    finally:
        worker.join(3)


@pytest.mark.parametrize(('body', 'headers', 'status', 'message'), [
    (b'123456', b'', b'200 OK', 'size limit'),
    (b'123456', b'Content-Length: 6\r\n', b'200 OK', 'size limit'),
    (b'', b'Content-Length: -1\r\n', b'200 OK', 'size limit'),
    (b'abc', b'Content-Encoding: gzip\r\n', b'200 OK', 'compressed'),
    (b'', b'Location: https://example.org/other\r\n', b'302 Found', 'redirect'),
])
def test_payload_policy_rejections(network, body, headers, status, message):
    response(network, body, headers, status)
    with pytest.raises(RuntimeError, match=message):
        http.fetch_public_bytes(URL, HOSTS, max_bytes=5, overall_timeout=1)
    assert network.closed.is_set()


@pytest.mark.parametrize('stage', ['headers', 'body', 'body_until_close'])
def test_cancellation_interrupts_an_http_wait(network, stage):
    cancelled = threading.Event()
    if stage == 'body':
        network.feed(b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\na')
    elif stage == 'body_until_close':
        network.feed(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\na')

    def cancel():
        assert network.requested.wait(1)
        time.sleep(.04)
        cancelled.set()

    worker = threading.Thread(target=cancel)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match='cancelled'):
            http.fetch_public_bytes(URL, HOSTS, cancel_event=cancelled, overall_timeout=2)
        assert time.monotonic() - started < .7
        assert network.closed.is_set()
    finally:
        worker.join(1)


@pytest.mark.parametrize('trickle', [False, True])
def test_overall_deadline_bounds_header_stall_and_slowloris(network, trickle):
    stopped = threading.Event()

    def feed():
        if network.requested.wait(1):
            network.feed(b'HTTP/1.1 200 OK\r\nX-Long: ')
            while not stopped.wait(.015):
                network.feed(b'a')

    worker = threading.Thread(target=feed) if trickle else None
    if worker:
        worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match='time limit'):
            http.fetch_public_bytes(URL, HOSTS, overall_timeout=.12)
        assert time.monotonic() - started < .7
        assert network.closed.is_set()
    finally:
        stopped.set()
        if worker:
            worker.join(1)


@pytest.mark.parametrize('stage', ['connect', 'TLS'])
@pytest.mark.parametrize('cancel', [False, True])
def test_connect_and_tls_waits_are_interrupted(network, monkeypatch, stage, cancel):
    entered = threading.Event()
    cancelled = threading.Event()

    def stall(*args):
        entered.set()
        with network.condition:
            while not network.eof:
                network.condition.wait(1)
        raise OSError('connection interrupted')

    monkeypatch.setattr(network, 'connect' if stage == 'connect' else 'do_handshake', stall)

    def stop():
        if entered.wait(1):
            cancelled.set()

    worker = threading.Thread(target=stop) if cancel else None
    if worker:
        worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match='cancelled' if cancel else 'time limit'):
            http.fetch_public_bytes(URL, HOSTS, cancel_event=cancelled,
                                    overall_timeout=1 if cancel else .1)
        assert time.monotonic() - started < .7
        assert network.closed.is_set()
    finally:
        network.finish()
        if worker:
            worker.join(1)


def test_overall_deadline_includes_a_slow_streamed_body(network):
    stopped = threading.Event()
    network.feed(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n')

    def feed():
        while not stopped.wait(.015):
            network.feed(b'x')

    worker = threading.Thread(target=feed)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match='time limit'):
            http.fetch_public_bytes(URL, HOSTS, overall_timeout=.12)
        assert time.monotonic() - started < .7
    finally:
        stopped.set()
        worker.join(1)


def test_cancelled_before_start_does_not_resolve(monkeypatch):
    event = threading.Event()
    event.set()
    calls = []
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError, match='cancelled'):
        http.fetch_public_json(URL, HOSTS, cancel_event=event, overall_timeout=.2)
    assert calls == []


def test_stalled_dns_is_bounded_and_does_not_start_unlimited_workers(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def resolve(*args, **kwargs):
        calls.append(args)
        entered.set()
        release.wait(2)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_IP, 443))]

    monkeypatch.setattr(http.socket, 'getaddrinfo', resolve)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match='time limit'):
            http.fetch_public_bytes(URL, HOSTS, overall_timeout=.07)
        assert entered.is_set()
        for _ in range(3):
            with pytest.raises(RuntimeError, match='time limit'):
                http.fetch_public_bytes(URL, HOSTS, overall_timeout=.04)
        assert len(calls) == 1
        assert time.monotonic() - started < .7
    finally:
        release.set()


def test_cancellation_during_dns_does_not_wait_for_resolver(monkeypatch):
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()

    def resolve(*args, **kwargs):
        entered.set()
        release.wait(2)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_IP, 443))]

    def cancel():
        entered.wait(1)
        cancelled.set()

    monkeypatch.setattr(http.socket, 'getaddrinfo', resolve)
    worker = threading.Thread(target=cancel)
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match='cancelled'):
            http.fetch_public_bytes(URL, HOSTS, cancel_event=cancelled, overall_timeout=2)
        assert time.monotonic() - started < .7
    finally:
        release.set()
        worker.join(1)


def test_mixed_public_private_dns_is_refused_before_connect(network, monkeypatch):
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))
        for ip in (PUBLIC_IP, '127.0.0.1')])
    with pytest.raises(ValueError, match='nonpublic'):
        http.fetch_public_bytes(URL, HOSTS, overall_timeout=1)
    assert network.address is None
    assert not network.request


@pytest.mark.parametrize('duration', [0, -1, 46, True, float('inf'), float('nan')])
def test_invalid_overall_deadline_refused(duration):
    with pytest.raises(ValueError, match='timeout'):
        http.fetch_public_bytes(URL, HOSTS, overall_timeout=duration)
