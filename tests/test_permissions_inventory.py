"""Synthetic policy/HTTP fixtures. No live model, owner or physical acceptance."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from prometheus_assistant import permissions_inventory as permissions
from prometheus_assistant.builtin_tools import build_builtin_registry
from prometheus_assistant.guardrails import Decision, GuardrailResult, ToolRequest, evaluate_tool_request
from prometheus_assistant.owner_access import OwnerAccess
from prometheus_assistant.ui_server import BROWSER_PERMISSIONS_POLICY, MODES, Server


def forbidden(*args, **kwargs):
    raise AssertionError('Read-only inventory attempted a side effect or private store access')


def test_inventory_uses_runtime_registry_and_evaluator_with_no_io_effects(monkeypatch):
    # Import before traps: discovery may read Python source but must not open data
    # stores, connect to a provider/runtime, start commands or execute a tool.
    permissions._registries()
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(Path, 'mkdir', forbidden)
    monkeypatch.setattr(Path, 'write_text', forbidden)
    monkeypatch.setattr(Path, 'write_bytes', forbidden)
    result = permissions.snapshot()
    tools = build_builtin_registry(None).structured_catalog()
    rows = {row['id']: row for row in result['rows']}
    assert result['read_only'] and not result['authorizes_execution']
    assert result['coverage']['complete'] is False
    assert result['coverage']['core_tools_evaluated'] == len(tools)
    assert result['coverage']['browser_modes_listed'] == len(MODES)
    assert {row['capability'].removeprefix('Browser: ') for row in rows.values()
            if row['id'].startswith('browser.')} == MODES
    for tool in tools:
        request = ToolRequest(tool['worker'], tool['name'], 'Test', **tool['risk'])
        assert rows['core.'+tool['worker']+'.'+tool['name']]['outcome'] == evaluate_tool_request(request).decision.value
    assert rows['core.knowledge.huggingface']['outcome'] == 'allow'
    assert rows['core.security.hardware']['outcome'] == 'allow'
    assert rows['core.project.tests']['outcome'] == 'approval'
    assert rows['core.research.public']['outcome'] == 'approval'
    assert rows['identity.owner']['outcome'] == 'not_observed'
    assert rows['devices.browser_capture']['reason'] == BROWSER_PERMISSIONS_POLICY
    assert rows['models.hosted']['outcome'] == 'unavailable'
    assert {r['category'] for r in rows.values()} == {'application', 'authentication_integrity', 'advisory', 'external'}
    assert len(result['policy_revision']) == 64
    assert all(not row['adjustable'] for row in rows.values())


def test_revision_is_stable_and_matches_current_source():
    first, second = permissions.snapshot(), permissions.snapshot()
    assert first['policy_revision'] == second['policy_revision']
    for name, digest in first['source_sha256'].items():
        assert hashlib.sha256(Path(permissions.__file__).with_name(name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('flags, expected', [
    ({}, Decision.ALLOW), ({'mutates_state': True}, Decision.APPROVAL),
    ({'external_network': True}, Decision.APPROVAL), ({'command_execution': True}, Decision.APPROVAL),
    ({'mutates_state': True, 'external_network': True, 'command_execution': True}, Decision.APPROVAL),
])
def test_existing_default_and_custom_deny_precedence_unchanged(flags, expected):
    request = ToolRequest('sample', 'sample', 'fixture', **flags)
    assert evaluate_tool_request(request).decision is expected
    assert evaluate_tool_request(request, [lambda _: GuardrailResult(Decision.DENY, 'fixture deny')]).decision is Decision.DENY


@pytest.mark.parametrize('body, outcome', [
    ({'worker': 'math', 'tool': 'calculate', 'arguments': {'expression': '1+2'}}, 'allow'),
    ({'worker': 'project', 'tool': 'tests', 'arguments': {'target': 'tests'}}, 'approval'),
    ({'worker': 'research', 'tool': 'public', 'arguments': {'provider': 'crossref', 'query': 'fixture'}}, 'approval'),
    ({'worker': 'project', 'tool': 'replace', 'arguments': {'path': 'fixture.py', 'old': 'a', 'new': 'b'}}, 'approval'),
    ({'worker': 'math', 'tool': 'calculate', 'arguments': {}}, 'deny'),
    ({'worker': 'math', 'tool': 'calculate', 'arguments': {'expression': 1}}, 'deny'),
    ({'worker': 'math', 'tool': 'calculate', 'arguments': {'expression': ''}}, 'deny'),
    ({'worker': 'math', 'tool': 'calculate', 'arguments': {'expression': 'x'*513}}, 'deny'),
    ({'worker': 'math', 'tool': 'calculate', 'arguments': []}, 'deny'),
    ({'worker': 'knowledge', 'tool': 'huggingface', 'arguments': None}, 'deny'),
    ({'worker': 'knowledge', 'tool': 'resources', 'arguments': None}, 'deny'),
    ({'worker': 'legal', 'tool': 'intake', 'arguments': None}, 'deny'),
    ({'worker': 'project', 'tool': 'list', 'arguments': None}, 'deny'),
    ({'worker': 'project', 'tool': 'tests', 'arguments': None}, 'deny'),
    ({'worker': 'security', 'tool': 'hardware', 'arguments': None}, 'deny'),
    ({'worker': 'system', 'tool': 'summary', 'arguments': None}, 'deny'),
    ({'worker': 'math', 'tool': 'calculate', 'arguments': {'expression': '1', 'approved': True}}, 'deny'),
    ({'worker': 'project', 'tool': 'tests', 'arguments': {}, 'command_execution': False}, 'deny'),
    ({'worker': 'project', 'tool': 'tests', 'arguments': {}, 'approved_request_ids': [0]}, 'deny'),
    ({'worker': 'not-registered', 'tool': 'secret-marker', 'arguments': {}}, 'deny'),
    ({'worker': [], 'tool': 'tests', 'arguments': {}}, 'deny'),
    ({'worker': 'x'*81, 'tool': 'tests', 'arguments': {}}, 'deny'),
    ([], 'deny'), (None, 'deny'), ({}, 'deny'),
])
def test_previews_validate_actual_schema_and_never_execute_or_echo(monkeypatch, body, outcome):
    registry = build_builtin_registry(None)
    for spec in registry._tools.values():
        object.__setattr__(spec, 'executor', forbidden)
    monkeypatch.setattr(permissions, '_registries', lambda: (registry, None))
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = permissions.preview_core(body)
    assert result['outcome'] == outcome
    assert result['authorizes_execution'] is False
    assert result['grant']['status'] == 'none_supplied'
    assert 'arguments' not in result
    assert 'secret-marker' not in json.dumps(result)


def test_preview_is_not_target_validation_or_a_replayable_grant(tmp_path):
    missing = tmp_path/'missing.py'
    body = {'worker': 'project', 'tool': 'read', 'arguments': {'path': str(missing)}}
    first = permissions.preview_core(body)
    assert first['outcome'] == 'allow' and not missing.exists()
    assert not first['authorizes_execution'] and 'approval_id' not in first
    # Repeated previews create neither a plan nor an approval.
    assert permissions.preview_core(body) == first


@pytest.mark.parametrize('state, expected', [
    ({'required': False, 'ready': False, 'unlocked': True, 'expires_in_seconds': None}, 'not_configured'),
    ({'required': True, 'ready': False, 'unlocked': False, 'expires_in_seconds': None}, 'unavailable'),
    ({'required': True, 'ready': True, 'unlocked': False, 'expires_in_seconds': None}, 'deny'),
    ({'required': True, 'ready': True, 'unlocked': True, 'expires_in_seconds': 75}, 'allow'),
])
def test_owner_outcomes_do_not_conflate_setup_permission_and_availability(state, expected):
    row = next(r for r in permissions.snapshot(owner=state)['rows'] if r['id'] == 'identity.owner')
    assert row['outcome'] == expected
    assert row['grant']['expires_in_seconds'] == (75 if expected == 'allow' else None)


def test_owner_observation_never_reads_keys_or_mutates_expired_sessions(tmp_path, monkeypatch):
    gate = OwnerAccess(tmp_path/'OwnerAccess', 'http://localhost:50000', clock=lambda: 10)
    gate.required = gate.ready = True
    gate.sessions = {'synthetic-cookie': 80, 'expired-cookie': 5}
    before = dict(gate.sessions)
    monkeypatch.setattr(gate, '_rows', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    state = gate.boundary_state('synthetic-cookie')
    assert state['unlocked'] and state['expires_in_seconds'] == 70
    assert 'synthetic-cookie' not in json.dumps(state)
    assert not gate.boundary_state('expired-cookie')['unlocked']
    assert gate.sessions == before
    gate.logout('synthetic-cookie')
    assert not gate.boundary_state('synthetic-cookie')['unlocked']


@pytest.fixture
def endpoint(tmp_path):
    app = SimpleNamespace(memory_path=tmp_path/'memory.sqlite3', token='synthetic-request-token', submit=forbidden)
    server = Server(('127.0.0.1', 0), app)
    server.owner_access.required = server.owner_access.ready = True
    server.owner_access.sessions['synthetic-owner-session'] = server.owner_access.clock()+120
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, body=None, *, token=True, cookie=True, origin=None, host=None, fetch_site=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        headers = {'Host': host or server.owner_origin.removeprefix('http://'),
                   'Origin': origin or server.owner_origin, 'Content-Type': 'application/json'}
        if token: headers['X-Prometheus-Token'] = app.token
        if cookie: headers['Cookie'] = 'prometheus_owner=synthetic-owner-session'
        if fetch_site: headers['Sec-Fetch-Site'] = fetch_site
        try:
            connection.request('GET' if body is None else 'POST', path,
                               body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
    yield server, request
    server.shutdown(); server.server_close(); thread.join(3)


def test_http_inventory_and_preview_share_owner_token_origin_gates(endpoint):
    server, request = endpoint
    body = {'worker': 'project', 'tool': 'tests', 'arguments': {}}
    for path, payload in [('/api/permissions', None), ('/api/permissions/preview', body)]:
        assert request(path, payload, token=False)[0] == 403
        assert request(path, payload, origin='https://foreign.example')[0] == 403
        assert request(path, payload, host='foreign.example')[0] == 403
        assert request(path, payload, fetch_site='cross-site')[0] == 403
        assert request(path, payload, cookie=False)[0] == 401
    status, headers, data = request('/api/permissions')
    assert status == 200 and headers['Cache-Control'] == 'no-store'
    assert headers['Permissions-Policy'] == BROWSER_PERMISSIONS_POLICY
    assert json.loads(data)['coverage']['complete'] is False
    assert request('/permissions.js')[0] == 200
    status, _, result = request('/api/permissions/preview', body)
    assert status == 200 and json.loads(result)['outcome'] == 'approval'
    assert request('/api/permissions/preview', {'grant': 'everything'})[0] == 200
    assert json.loads(request('/api/permissions/preview', {'grant': 'everything'})[2])['outcome'] == 'deny'
    status, _, result = request('/api/permissions/preview', {'worker': 'project', 'tool': 'tests', 'arguments': None})
    assert status == 200
    assert json.loads(result)['outcome'] == 'deny'
    assert json.loads(result)['authorizes_execution'] is False
    # Real handler lock invalidates the fixture session; neither endpoint accepts it afterwards.
    assert request('/api/owner/lock', {})[0] == 200
    assert request('/api/permissions')[0] == 401
    assert request('/api/permissions/preview', body)[0] == 401


def test_http_expired_session_and_unknown_route(endpoint):
    server, request = endpoint
    assert request('/api/permissions/unknown')[0] == 404
    assert request('/api/permissions', {})[0] == 404  # There is no settings-write endpoint.
    server.owner_access.sessions['synthetic-owner-session'] = server.owner_access.clock()-1
    assert request('/api/permissions')[0] == 401


def test_cli_permissions_does_not_create_memory(tmp_path):
    memory = tmp_path/'never-created'/'memory.sqlite3'
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-B', str(root/'prometheus.py'), '--memory', str(memory), 'permissions'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['read_only'] and not memory.parent.exists()
    assert next(row for row in data['rows'] if row['id'] == 'identity.owner')['outcome'] == 'not_observed'
