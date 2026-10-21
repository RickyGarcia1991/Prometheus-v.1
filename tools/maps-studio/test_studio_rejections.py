"""Rejected POSTs keep their exact status and bounded transport cleanup."""
import io
import http.client
import socket
import threading
import time
from email.message import Message
from unittest.mock import Mock

import pytest

from test_studio import studio


def handler(length='2', body=b'{}', **headers):
    obj = object.__new__(studio.Handler)
    obj.headers = Message()
    obj.headers['Content-Length'] = length
    for name, value in headers.items():
        obj.headers[name] = value
    obj.rfile = io.BytesIO(body)
    obj.wfile = io.BytesIO()
    obj.connection = Mock()
    obj.connection.gettimeout.return_value = 8
    obj.allowed = Mock(return_value=False)
    obj.respond = Mock()
    obj.server = Mock()
    return obj


def test_denied_post_preserves_403_and_consumes_small_unread_body():
    obj = handler()
    obj.do_POST()
    obj.respond.assert_called_once_with(403, {'error': 'This action requires the local session.'})
    assert obj.rfile.read() == b''
    obj.server.services.request.assert_not_called()


def test_denied_post_never_consumes_more_than_bounded_cleanup():
    obj = handler('1000000', b'x' * 1000000)
    obj.do_POST()
    assert 0 < obj.rfile.tell() <= 65536
    obj.respond.assert_called_once_with(403, {'error': 'This action requires the local session.'})


@pytest.mark.parametrize('length', ['-1', 'abc', '100000000000000000000000', '0'])
def test_invalid_or_empty_length_does_not_read(length):
    obj = handler(length)
    obj.do_POST()
    assert obj.rfile.tell() == 0
    obj.respond.assert_called_once_with(403, {'error': 'This action requires the local session.'})


def test_ambiguous_length_or_transfer_encoding_does_not_read():
    for duplicate in (False, True):
        obj = handler()
        if duplicate:
            obj.headers['Content-Length'] = '3'
        else:
            obj.headers['Transfer-Encoding'] = 'chunked'
        obj.do_POST()
        assert obj.rfile.tell() == 0


def test_slow_body_timeout_keeps_rejection_and_restores_timeout():
    obj = handler()
    obj.rfile = Mock()
    obj.rfile.read1.side_effect = TimeoutError('generated slow client')
    obj.do_POST()
    obj.respond.assert_called_once_with(403, {'error': 'This action requires the local session.'})
    obj.connection.settimeout.assert_called()
    assert obj.connection.settimeout.call_args.args == (8,)


def test_trickle_body_uses_total_deadline(monkeypatch):
    obj = handler('1000', b'x' * 1000)
    ticks = iter([0, 0, .25])
    monkeypatch.setattr(studio.time, 'monotonic', lambda: next(ticks))
    obj.rfile = Mock()
    obj.rfile.read1.return_value = b'x'
    obj.do_POST()
    assert obj.rfile.read1.call_count == 1


def test_bad_content_type_preserves_415_without_authorized_execution():
    obj = handler(**{'Content-Type': 'text/plain'})
    obj.allowed.return_value = True
    obj.do_POST()
    obj.respond.assert_called_once_with(415, {'error': 'Use JSON with a content length.'})
    assert obj.rfile.read() == b''
    obj.server.services.request.assert_not_called()


@pytest.mark.parametrize('send_body', [False, True])
def test_real_rejection_is_sent_before_body_and_connection_closes_promptly(send_body):
    server = studio.Server(('127.0.0.1', 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        server.services.request = Mock(side_effect=AssertionError('Rejected request dispatched'))
        with socket.create_connection(server.server_address, timeout=2) as connection:
            connection.sendall((f'POST /api/review HTTP/1.0\r\nHost: {server.origin[7:]}\r\n'
                                'Origin: https://evil.example\r\nContent-Type: application/json\r\n'
                                'Content-Length: 2\r\n\r\n').encode())
            started = time.monotonic()
            response = http.client.HTTPResponse(connection)
            response.begin()
            assert response.status == 403
            assert b'local session' in response.read()
            if send_body:
                connection.sendall(b'{}')
            assert connection.recv(1) == b''
            assert time.monotonic() - started < 1.5
        server.services.request.assert_not_called()
        assert not server.stop_requested
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
