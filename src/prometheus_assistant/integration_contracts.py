"""Canonical task access envelopes shared by review, grants and dispatch.

No model output or natural-language approval is interpreted here. Only the
authenticated approval consumer issues a grant from one of these contracts.
"""
import hashlib
import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

TASK_CAPABILITIES = frozenset({'integrations.agentmemory', 'integrations.browser_use', 'integrations.agentmemory_service'})
MEMORY_ADAPTERS = frozenset({'agentmemory', 'agentmemory_service'})
_TASK_KEYS = {'task_id', 'task', 'adapter', 'actions', 'resources', 'duration_seconds'}
_ACTIONS = {'agentmemory': {'remember', 'recall', 'forget'},
            'agentmemory_service': {'remember', 'recall', 'forget'}, 'browser_use': {'read'}}


def canonical_host(value):
    if not isinstance(value, str) or not value or len(value) > 253 or value != value.strip():
        raise ValueError('Choose a bounded public hostname without a URL or wildcard.')
    try:
        host = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ValueError('The task hostname is invalid.') from None
    labels = host.split('.')
    if (len(host) > 253 or len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', x) for x in labels)
            or labels[-1] in {'local', 'localhost', 'internal', 'home', 'lan', 'test', 'invalid', 'onion'}):
        raise ValueError('Choose a public hostname without local names, ports or wildcards.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError('Task host envelopes cannot contain IP addresses.')


def canonical_task(value):
    if not isinstance(value, dict) or set(value) != _TASK_KEYS:
        raise ValueError('Use the complete supported task access contract.')
    identity, task, adapter = value['task_id'], value['task'], value['adapter']
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('A fresh task identifier is required.')
    if (not isinstance(task, str) or not task.strip() or len(task) > 1000
            or any(ord(c) < 32 and c not in '\n\t' for c in task)):
        raise ValueError('Describe the owner-reviewed task in 1 to 1000 characters.')
    if not isinstance(adapter, str) or adapter not in _ACTIONS:
        raise ValueError('Choose an available integration adapter.')
    actions = value['actions']
    if (not isinstance(actions, list) or not actions or len(actions) > 3
            or any(not isinstance(x, str) or x not in _ACTIONS[adapter] for x in actions)
            or len(set(actions)) != len(actions)):
        raise ValueError('Choose distinct supported task operations.')
    resources = value['resources']
    if not isinstance(resources, dict):
        raise ValueError('Declare the task access envelope.')
    if adapter in MEMORY_ADAPTERS:
        if set(resources) != {'project'}:
            raise ValueError('A memory task must name exactly one project.')
        from .agent_memory import canonical_arguments
        project = canonical_arguments('recall', {'project': resources['project'], 'query': 'scope'})['project']
        selected = {'project': project}
    else:
        if set(resources) == {'public_web'} and resources['public_web'] is True:
            selected = {'public_web': True}
        else:
            if set(resources) != {'hosts'} or not isinstance(resources['hosts'], list) or not 1 <= len(resources['hosts']) <= 64:
                raise ValueError('A browser task must declare exact public hosts or explicit public_web access.')
            hosts = [canonical_host(host) for host in resources['hosts']]
            if len(set(hosts)) != len(hosts):
                raise ValueError('Task hosts must be distinct after normalization.')
            selected = {'hosts': sorted(hosts)}
    seconds = value['duration_seconds']
    # JSON/SQLite timestamps must remain finite and exactly representable. This
    # technical bound is deliberately unrelated to legacy 15-minute grants.
    if seconds is not None and (type(seconds) is not int or not 1 <= seconds <= 2 ** 53):
        raise ValueError('Use a positive, representable duration in seconds, or null for until revoked.')
    return {'task_id': identity, 'task': task.strip(), 'adapter': adapter,
            'actions': sorted(actions), 'resources': selected, 'duration_seconds': seconds}


def canonical_arguments(adapter, action, arguments):
    if adapter == 'agentmemory':
        from .agent_memory import canonical_arguments as canonical
    elif adapter == 'agentmemory_service':
        from .agent_memory_service import canonical_arguments as canonical
    elif adapter == 'browser_use':
        from .browser_agent import canonical_arguments as canonical
    else:
        raise ValueError('Choose an available integration adapter.')
    return canonical(action, arguments)


def allows_task(contract, adapter, action, arguments):
    """Return false on a mismatch; never expands a task from a tool's request."""
    try:
        contract = canonical_task(contract)
        if adapter != contract['adapter'] or action not in contract['actions']:
            return False
        args = canonical_arguments(adapter, action, arguments)
        if adapter in MEMORY_ADAPTERS:
            return args['project'] == contract['resources']['project']
        if contract['resources'].get('public_web') is True:
            # The provider's canonical URL validation above still applies;
            # dispatch separately resolves and pins public network addresses.
            return True
        return canonical_host(urlsplit(args['url']).hostname) in contract['resources']['hosts']
    except (ValueError, TypeError, KeyError):
        return False


def task_target(contract):
    return json.dumps({'kind': 'integration-task-v1', 'contract': canonical_task(contract)},
                      sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':'))


def task_digest(contract):
    return hashlib.sha256(task_target(contract).encode('utf-8')).hexdigest()


def decode_task_row(row):
    """Validate the witnessed row before granting any special clock semantics."""
    if row['capability'] not in TASK_CAPABILITIES or not isinstance(row['target'], str) or len(row['target']) > 32000:
        raise ValueError('Not a supported task grant.')
    try:
        target = json.loads(row['target'])
        if not isinstance(target, dict) or set(target) != {'kind', 'contract'} or target['kind'] != 'integration-task-v1':
            raise ValueError('Invalid task target.')
        contract = canonical_task(target['contract'])
        if (row['target'] != task_target(contract) or row['scope'] != task_digest(contract)
                or row['capability'] != 'integrations.' + contract['adapter'] or row['remaining'] != -1):
            raise ValueError('Task authority differs from its reviewed contract.')
        if not all(isinstance(row[key], (float, int)) and math.isfinite(row[key]) for key in ('created', 'expires', 'mono_expires')):
            raise ValueError('Invalid task time metadata.')
        seconds = contract['duration_seconds']
        if seconds is None:
            if row['expires'] != -1 or row['mono_expires'] != -1:
                raise ValueError('Invalid indefinite task metadata.')
        elif row['expires'] != row['created'] + seconds or row['mono_expires'] < 0:
            raise ValueError('Invalid finite task metadata.')
        return contract
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError('The stored task grant is invalid.') from None
