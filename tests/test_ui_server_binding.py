"""Native port ownership: concurrent previews must never share an endpoint."""
import socket
import http.client
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant.ui_server import Server


def test_second_server_cannot_share_active_preview_port(tmp_path):
    app = SimpleNamespace(memory_path=tmp_path / 'memory.sqlite3')
    first = Server(('127.0.0.1', 0), app)
    second = None
    try:
        with pytest.raises(OSError):
            second = Server(first.server_address, app)
    finally:
        if second is not None:
            second.server_close()
        first.server_close()


def test_reuse_address_cannot_displace_active_preview(tmp_path):
    app = SimpleNamespace(memory_path=tmp_path / 'memory.sqlite3')
    first = Server(('127.0.0.1', 0), app)
    try:
        with socket.socket() as contender:
            contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                contender.bind(first.server_address)
    finally:
        first.server_close()


def test_closed_preview_port_can_be_reopened(tmp_path):
    app = SimpleNamespace(memory_path=tmp_path / 'memory.sqlite3')
    first = Server(('127.0.0.1', 0), app)
    address = first.server_address
    first.server_close()
    replacement = Server(address, app)
    try:
        assert replacement.server_address == address
    finally:
        replacement.server_close()


def test_preview_port_reopens_after_completed_http_request(tmp_path):
    app = SimpleNamespace(memory_path=tmp_path / 'memory.sqlite3')
    first = Server(('127.0.0.1', 0), app)
    address = first.server_address
    first.timeout = 3
    listener = threading.Thread(target=first.handle_request)
    listener.start()
    client = http.client.HTTPConnection(*address, timeout=3)
    try:
        # Exercise the real handler without creating an owner session.
        client.request('GET', '/', headers={'Host': 'not-this-local-app.example'})
        response = client.getresponse()
        assert response.status == 403
        response.read()
    finally:
        client.close()
        listener.join(timeout=4)
        first.server_close()
    assert not listener.is_alive()
    replacement = Server(address, app)
    replacement.server_close()
