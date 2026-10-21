"""Opt-in device controls through Home Assistant's documented REST API.

Only explicit Smart home mode invokes this module. Model output and retrieved
documents never become commands. Pairing and device permissions stay with the hub.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import hashlib
import http.client
import secrets
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE = 1_000_000
DOMAINS = {
    'light': {'on', 'off', 'brightness', 'color', 'status'},
    'switch': {'on', 'off', 'status'},
    'fan': {'on', 'off', 'status'},
    'media_player': {'play', 'pause', 'stop', 'volume', 'status'},
    'climate': {'temperature', 'status'},
    'scene': {'activate', 'status'},
    'button': {'run', 'status'},
    'notify': {'announce'},
    'sensor': {'status'},
    'binary_sensor': {'status'},
}
COLORS = {'red': [255, 0, 0], 'green': [0, 255, 0], 'blue': [0, 0, 255],
          'white': [255, 255, 255], 'yellow': [255, 255, 0], 'purple': [128, 0, 128],
          'orange': [255, 165, 0]}
HELP = ('Smart home is on standby until you pair a Home Assistant hub and allow named devices. '
        'Use SmartHome/config.json beside your memory database; see docs/SMART-HOME.md. '
        'Examples: turn off bedroom lights; turn on front porch lights; '
        'set bedroom lights brightness to 40%; pause living room speaker; '
        'status front porch lights. Use devices to list configured aliases. '
        'Alexa Drop In calls remain in the Alexa app or on an Echo; this connector does not open microphones.')


class HomeError(ValueError):
    pass


def normalize(value):
    return ' '.join(value.casefold().split())


def validate_endpoint(value, allow_http=False, allow_remote=False):
    if not isinstance(value, str) or len(value) > 300:
        raise HomeError('Set a valid Home Assistant origin in the local configuration.')
    try:
        url = urllib.parse.urlsplit(value)
        port = url.port
    except ValueError as error:
        raise HomeError('Invalid Home Assistant port.') from error
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in {'', '/'} or (port is not None and not 1 <= port <= 65535)):
        raise HomeError('Use only the Home Assistant origin, without credentials, paths or query parameters.')
    try:
        ip = ipaddress.ip_address(url.hostname)
        local = ip.is_loopback or (ip.version == 4 and any(ip in ipaddress.ip_network(net)
                 for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
    except ValueError:
        local = False
    if url.scheme == 'http' and not (allow_http is True and local):
        raise HomeError('HTTP requires explicit allow_insecure_http and a private LAN or loopback IP. Prefer HTTPS.')
    if url.scheme == 'https' and not local and allow_remote is not True:
        raise HomeError('A hostname or remote HTTPS origin requires explicit allow_remote_https in local configuration.')
    return value.rstrip('/')


def load_config(root):
    path = Path(root) / 'config.json'
    if not path.is_file():
        return {'enabled': False, 'devices': {}}
    if path.stat().st_size > 65536:
        raise HomeError('Smart home configuration is too large.')
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, RecursionError) as error:
        raise HomeError('Smart home configuration could not be read.') from error
    if not isinstance(config, dict) or set(config) - {'enabled', 'endpoint', 'allow_insecure_http', 'allow_remote_https', 'devices', 'control_key_sha256'}:
        raise HomeError('Unknown smart home configuration fields. Credentials belong in Windows Credential Manager.')
    if config.get('enabled') is not True:
        return {'enabled': False, 'devices': {}}
    if not isinstance(config.get('control_key_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', config['control_key_sha256']):
        raise HomeError('Pair a private browser control key using tools/Set-HomeAssistant-Credential.ps1 before enabling control.')
    config['endpoint'] = validate_endpoint(config.get('endpoint'), config.get('allow_insecure_http'), config.get('allow_remote_https'))
    devices = config.get('devices')
    if not isinstance(devices, dict) or not 1 <= len(devices) <= 100:
        raise HomeError('Configure 1–100 named devices.')
    validated = {}
    for alias, entry in devices.items():
        name = normalize(alias)
        if not re.fullmatch(r'[a-z0-9][a-z0-9 -]{0,79}', name) or name in validated or not isinstance(entry, dict):
            raise HomeError('Use unique plain device names of at most 80 characters.')
        if set(entry) - {'entity_id', 'actions', 'minimum', 'maximum', 'unit'}:
            raise HomeError('Unknown device configuration fields.')
        entity = entry.get('entity_id')
        if not isinstance(entity, str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', entity):
            raise HomeError('Each device needs a valid Home Assistant entity ID.')
        domain = entity.split('.')[0]
        actions = entry.get('actions')
        if (domain not in DOMAINS or not isinstance(actions, list) or not actions
                or any(not isinstance(action, str) or action not in DOMAINS[domain] for action in actions)):
            raise HomeError('A device requests unsupported actions. Locks, alarms, cameras and intercom calls are not enabled.')
        if 'temperature' in actions:
            lo, hi = entry.get('minimum'), entry.get('maximum')
            if (type(lo) not in {int, float} or type(hi) not in {int, float}
                    or not -20 <= lo < hi <= 100 or entry.get('unit') not in {'C', 'F'}):
                raise HomeError('Thermostats require minimum, maximum and C/F unit limits matching the hub.')
        validated[name] = dict(entry)
    config['devices'] = validated
    return config


@dataclass(frozen=True)
class Plan:
    alias: str
    entity_id: str
    action: str
    domain: str
    service: str | None
    data: dict


def parse_command(command, devices):
    raw = command.strip()
    text = normalize(raw).rstrip('.!?')
    action = alias = value = None
    patterns = [
        (r'(?:turn|switch) (on|off) (?:the )?(.+)', lambda m: (m[1], m[2], None)),
        (r'(?:turn|switch|shut) (?:the )?(.+?) (on|off)', lambda m: (m[2], m[1], None)),
        (r'(?:dim|brighten) (?:the )?(.+?) to (\d{1,3})(?:%| percent)', lambda m: ('brightness', m[1], int(m[2]))),
        (r'set (?:the )?(.+?) (brightness|volume) to (\d{1,3})(?:%| percent)', lambda m: (m[2], m[1], int(m[3]))),
        (r'set (?:the )?(.+?) temperature to (-?\d{1,3}(?:\.\d)?)', lambda m: ('temperature', m[1], float(m[2]))),
        (r'set (?:the )?(.+?) color to ([a-z]+)', lambda m: ('color', m[1], m[2])),
        (r'(play|pause|stop|activate|run|status) (?:the )?(.+)', lambda m: (m[1], m[2], None)),
    ]
    if text.startswith('announce on '):
        match = re.fullmatch(r'announce on (.+?):\s*(.+)', raw, re.I)
        if match:
            action, alias, value = 'announce', normalize(match[1]), match[2]
            if len(value) > 300 or any(c in value for c in '<>') or any(ord(c) < 32 for c in value):
                raise HomeError('Announcements must be plain text, at most 300 characters, without markup.')
    else:
        for pattern, extract in patterns:
            match = re.fullmatch(pattern, text)
            if match:
                action, alias, value = extract(match)
                break
    if action is None or alias not in devices:
        raise HomeError('Use an exact configured device name and supported command. Try devices or help. Nothing was sent.')
    entry = devices[alias]
    if action not in entry['actions']:
        raise HomeError(f'{action} is not enabled for {alias}. Nothing was sent.')
    domain = entry['entity_id'].split('.')[0]
    data = {'entity_id': entry['entity_id']}
    service = {'on': 'turn_on', 'off': 'turn_off', 'brightness': 'turn_on', 'color': 'turn_on',
               'play': 'media_play', 'pause': 'media_pause', 'stop': 'media_stop',
               'volume': 'volume_set', 'temperature': 'set_temperature',
               'activate': 'turn_on', 'run': 'press', 'announce': 'send_message', 'status': None}[action]
    if action in {'brightness', 'volume'}:
        if not 0 <= value <= 100:
            raise HomeError('Brightness and volume must be between 0 and 100 percent. Nothing was sent.')
        data['brightness_pct' if action == 'brightness' else 'volume_level'] = value if action == 'brightness' else value / 100
    elif action == 'temperature':
        if not entry['minimum'] <= value <= entry['maximum']:
            raise HomeError('Temperature is outside this device’s configured limits. Nothing was sent.')
        data['temperature'] = value
    elif action == 'color':
        if value not in COLORS:
            raise HomeError('Supported colors: ' + ', '.join(COLORS) + '. Nothing was sent.')
        data['rgb_color'] = COLORS[value]
    elif action == 'announce':
        data['message'] = value
    return Plan(alias, entry['entity_id'], action, domain, service, data)


def validate_credential_record(record, config):
    if (not isinstance(record, dict) or record.get('endpoint') != config['endpoint']
            or record.get('control_key_sha256') != config['control_key_sha256']):
        raise HomeError('The saved credential belongs to a different hub or pairing. Pair this configuration again. No token was sent.')
    token = record.get('token')
    if not isinstance(token, str) or not token or len(token) > 2000 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise HomeError('The saved Home Assistant credential is invalid; pair again.')
    return token


def credential_token(config):
    """Read a host-bound generic credential; never return it to UI/logs."""
    if os.name != 'nt':
        raise HomeError('This release uses Windows Credential Manager for Home Assistant pairing.')
    import ctypes
    from ctypes import wintypes
    class Credential(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
            ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
            ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
            ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
            ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
    advapi = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
    pointer = ctypes.POINTER(Credential)()
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    if not advapi.CredReadW('Prometheus/HomeAssistant', 1, 0, ctypes.byref(pointer)):
        raise HomeError('Pair Home Assistant on this Windows account using tools/Set-HomeAssistant-Credential.ps1.')
    try:
        try:
            record = json.loads(ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError):
            raise HomeError('Pair Home Assistant again; the credential record is invalid.') from None
        return validate_credential_record(record, config)
    finally:
        advapi.CredFree(pointer)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise HomeError('Home Assistant redirected the request. Correct the configured origin; no credential was forwarded.')


class HomeClient:
    def __init__(self, endpoint, token):
        self.endpoint, self._token = endpoint, token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, body=None):
        request = urllib.request.Request(self.endpoint + path,
            data=None if body is None else json.dumps(body).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise HomeError('The hub response exceeded its size limit.')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            raise HomeError(f'Home Assistant returned HTTP {error.code}. Check pairing and entity access. The action was not retried.') from None
        except (OSError, urllib.error.URLError, ValueError, RecursionError, http.client.HTTPException) as error:
            if isinstance(error, HomeError):
                raise
            raise HomeError('The hub response could not be confirmed. Check the device before retrying; actions are never retried automatically.') from None


def handle_command(prompt, root, *, control_key='', client_factory=HomeClient, token_reader=credential_token):
    try:
        if normalize(prompt) in {'help', 'setup', 'capabilities'}:
            return {'reply': HELP, 'connected': False}
        if re.search(r'\bdrop[ -]?in\b|\bintercom\b', prompt, re.I):
            return {'reply': 'Alexa Drop In is not enabled through Prometheus. Use your Alexa app or say “Alexa, drop in on [device]” on an authorized Echo. Drop In status controls do not establish a two-way call.', 'connected': False}
        config = load_config(root)
        if not config['enabled']:
            return {'reply': HELP, 'standby': True}
        if (not isinstance(control_key, str) or not 20 <= len(control_key) <= 128
                or not secrets.compare_digest(hashlib.sha256(control_key.encode()).hexdigest(), config['control_key_sha256'])):
            raise HomeError('Enter your private smart-home control key in the Smart home field. No device was contacted.')
        devices = config['devices']
        if normalize(prompt) in {'devices', 'list devices'}:
            return {'reply': '\n'.join(f'{alias}: {", ".join(entry["actions"])}' for alias, entry in devices.items()), 'configured': True}
        commands = prompt.split(';')
        if not 1 <= len(commands) <= 4 or any(not item.strip() for item in commands):
            raise HomeError('Use 1–4 commands separated by semicolons. Nothing was sent.')
        # Validate the whole batch before any network effect.
        plans = [parse_command(command, devices) for command in commands]
        client = client_factory(config['endpoint'], token_reader(config))
        replies = []
        for plan in plans:
            try:
                if plan.action == 'temperature':
                    settings = client.request('/api/config')
                    if (not isinstance(settings, dict) or not isinstance(settings.get('unit_system'), dict)
                            or settings['unit_system'].get('temperature') != '°' + devices[plan.alias]['unit']):
                        raise HomeError('The thermostat unit could not be confirmed. No temperature command was sent.')
                if plan.service:
                    client.request('/api/services/' + plan.domain + '/' + plan.service, plan.data)
                if plan.action == 'status' or plan.domain not in {'notify', 'button', 'scene'}:
                    state = client.request('/api/states/' + plan.entity_id)
                    if not isinstance(state, dict) or not isinstance(state.get('state'), str):
                        raise HomeError('The hub did not return a valid device state.')
                    replies.append(f'{plan.alias}: hub reports {state["state"][:80]} after {plan.action}.')
                else:
                    replies.append(f'{plan.alias}: hub accepted {plan.action}; physical completion is not confirmed.')
            except HomeError as error:
                replies.append(f'{plan.alias}: {error} Remaining commands were not sent.')
                break
        return {'reply': '\n'.join(replies), 'provider': 'Home Assistant'}
    except (HomeError, OSError) as error:
        return {'reply': str(error), 'connected': False}
