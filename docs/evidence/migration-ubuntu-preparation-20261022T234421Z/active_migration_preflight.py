"""Pure, fail-closed validation of inspect-only Windows observations. No IO."""
import ntpath
import re

R5 = 'b93d865e8560fad4ba2f5e7d91e5758400d2d864ae8f07fd0438c00f56ccd77e'
R7 = '6e26224389829b4f9cfb3288b8f9495d0e285e9fc96a322f3db25e82ecddc01e'
PAYLOADS = {'Prometheus-QuietHost-v3.exe', 'Prometheus-USB-Reconnect-Supervisor.ps1', 'Prometheus-Lifecycle.ps1', 'Prometheus-Arrival.ps1', 'Prometheus-Host-Agent.ps1'}


def exact(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError('Observation schema mismatch')


def string(value, limit=4096):
    if type(value) is not str or not 0 < len(value) <= limit or '\x00' in value:
        raise ValueError('Bounded observation string required')
    return value


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Observation integer required')


def boolean(value):
    if type(value) is not bool:
        raise ValueError('Observation boolean required')


def hash_value(value):
    string(value, 64)
    if not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Observation SHA256 required')


def path(value):
    string(value)
    if not ntpath.isabs(value) or '..' in value.replace('/', '\\').split('\\'):
        raise ValueError('Absolute physical observation path required')
    return ntpath.normcase(ntpath.normpath(value))


def strings(value):
    if type(value) is not list or len(value) > 128:
        raise ValueError('Bounded observation array required')
    for item in value:
        string(item)


def result(status, reasons, phase='unobserved', evidence=None):
    return dict(status=status, reasons=reasons, current_runtime_phase=phase, can_execute=False, mutation_ready=False, production_ready=False, supported_contract='cooperative-maintenance-in-one-interactive-session', stale_observation_caveat='Execution must repeat observations under maintenance locks; this snapshot grants no mutation authority.', cross_session_exclusion_proven=False, inspection_only=True, evidence=evidence or {})


def evaluate(observation, sessions):
    try:
        return _evaluate(observation, sessions)
    except (ValueError, TypeError, KeyError) as error:
        return result('incomplete_context', [str(error)])


def _evaluate(o, sessions):
    if type(o) is not dict or type(o.get('collected')) is not bool or o.get('schema') != 1 or type(o.get('schema')) is not int:
        raise ValueError('Collector schema/collection flag invalid')
    if not o['collected']:
        exact(o, 'schema collected stage error context')
        return result('incomplete_context', [string(o['stage']) + ': ' + string(o['error'])], evidence={'context': o['context']})
    exact(o, 'schema collected observed_utc context packages receipt auth task runtime workers pause_markers migration_evidence competing_lifecycle_tasks')
    string(o['observed_utc'])
    c = o['context']
    exact(c, 'sid session_id profile_root physical_local reported_local root classification')
    if not re.fullmatch(r'S-1-\d+(?:-\d+)+', string(c['sid'], 256)):
        raise ValueError('Current SID malformed')
    integer(c['session_id'])
    reasons = []
    if c['classification'] != 'physical-profile':
        reasons.append('MSIX/redirected or unsupported profile context')
    profile, physical, reported, root = [path(c[name]) for name in ('profile_root', 'physical_local', 'reported_local', 'root')]
    if physical != ntpath.join(profile, 'appdata', 'local') or reported != physical or root != ntpath.join(physical, 'prometheus'):
        reasons.append('Physical profile and observed LocalAppData disagree')
    exact(sessions, 'complete current_session rows')
    boolean(sessions['complete']); integer(sessions['current_session'])
    if type(sessions['rows']) is not list or len(sessions['rows']) > 128:
        raise ValueError('Bounded session rows required')
    owned_sessions = []
    seen = set()
    for row in sessions['rows']:
        exact(row, 'session_id sid state')
        integer(row['session_id']); string(row['sid'], 256); string(row['state'])
        if row['session_id'] in seen:
            raise ValueError('Duplicate session observation')
        seen.add(row['session_id'])
        if row['sid'] == c['sid']:
            owned_sessions.append(row)
    if not sessions['complete'] or sessions['current_session'] != c['session_id']:
        reasons.append('Session enumeration incomplete or collector session changed')
    if len(owned_sessions) != 1 or owned_sessions[0]['session_id'] != c['session_id'] or owned_sessions[0]['state'] != 'active':
        reasons.append('Only one observed active current-user session is supported; this snapshot does not exclude future sessions')
    p = o['packages']; exact(p, 'r5 r7 entries unchanged')
    hash_value(p['r5']); hash_value(p['r7']); integer(p['entries']); integer(p['unchanged'])
    if p != dict(r5=R5, r7=R7, entries=7, unchanged=6):
        reasons.append('Exact signed R5/R7 HostAgent-only closure required')
    receipt = o['receipt']; exact(receipt, 'id sha256 phase package authenticated')
    if not re.fullmatch('[a-f0-9]{32}', string(receipt['id'], 32)):
        raise ValueError('Original enrollment id malformed')
    hash_value(receipt['sha256']); hash_value(receipt['package']); boolean(receipt['authenticated']); string(receipt['phase'])
    if not receipt['authenticated'] or receipt['phase'] != 'enrolled-disabled' or receipt['package'] != R5:
        reasons.append('Original completed authenticated R5 receipt required')
    auth = o['auth']; exact(auth, 'sha256 sidecar_matches host_matches')
    hash_value(auth['sha256']); boolean(auth['sidecar_matches']); boolean(auth['host_matches'])
    if not auth['sidecar_matches'] or not auth['host_matches']:
        reasons.append('Authorization hash/host binding mismatch')
    task = o['task']; exact(task, 'name path enrollment_id config_sha256 enabled state owned_shape')
    for key in ('name', 'path', 'enrollment_id', 'state'):
        string(task[key])
    hash_value(task['config_sha256']); boolean(task['enabled']); boolean(task['owned_shape'])
    if task['name'] != 'Prometheus USB Reconnect Supervisor' or task['path'] != '\\' or task['enrollment_id'] != receipt['id'] or not task['owned_shape']:
        reasons.append('Existing task identity/definition does not match original enrollment')
    if task['state'] not in ('Running', 'Ready', 'Disabled'):
        reasons.append('Scheduler state is incomplete or transitioning')
    if (task['state'] == 'Disabled' and task['enabled']) or (task['state'] == 'Ready' and not task['enabled']) or (task['state'] == 'Running' and not task['enabled']):
        reasons.append('Unsupported/inconsistent task enabled and running state')
    if type(o['runtime']) is not list or len(o['runtime']) != 5:
        raise ValueError('Exactly five runtime observations required')
    runtime = {}
    for row in o['runtime']:
        exact(row, 'name sha256 r5_sha256 r7_sha256')
        if row['name'] not in PAYLOADS or row['name'] in runtime:
            raise ValueError('Runtime closure name mismatch/duplicate')
        for key in ('sha256', 'r5_sha256', 'r7_sha256'):
            hash_value(row[key])
        runtime[row['name']] = row
    changed = [name for name, row in runtime.items() if row['r5_sha256'] != row['r7_sha256']]
    if changed != ['Prometheus-Host-Agent.ps1']:
        reasons.append('Runtime reference closure has an unauthorized delta')
    if all(row['sha256'] == row['r5_sha256'] for row in runtime.values()):
        phase = 'original_r5'
    elif all(row['sha256'] == row['r7_sha256'] for row in runtime.values()):
        phase = 'r7_runtime_requires_completed_chain'
    else:
        phase = 'mixed_or_foreign_runtime'
    if phase != 'original_r5':
        reasons.append('Current runtime is not unchanged R5; require authenticated recovery/completion verification')
    if type(o['workers']) is not list or len(o['workers']) > 128:
        raise ValueError('Bounded worker observations required')
    wrappers, children = [], []
    seen_pids = set()
    for row in o['workers']:
        exact(row, 'kind pid parent_pid creation_ticks sid session_id image image_sha256 command_valid')
        for key in ('pid', 'creation_ticks'):
            integer(row[key], 1)
        for key in ('parent_pid', 'session_id'):
            integer(row[key])
        string(row['sid']); path(row['image']); hash_value(row['image_sha256']); boolean(row['command_valid'])
        if row['pid'] in seen_pids:
            raise ValueError('Duplicate worker PID')
        seen_pids.add(row['pid'])
        if row['sid'] != c['sid'] or row['session_id'] != c['session_id']:
            reasons.append('Owned worker outside supported current SID/session')
        if row['kind'] == 'wrapper':
            wrappers.append(row)
            if path(row['image']) != ntpath.join(root, 'prometheus-quiethost-v3.exe') or row['image_sha256'] != runtime['Prometheus-QuietHost-v3.exe']['r5_sha256'] or not row['command_valid']:
                reasons.append('Wrapper image/command differs from signed task ownership')
        elif row['kind'] == 'supervisor':
            children.append(row)
            if not row['command_valid']:
                reasons.append('Supervisor executable/command ownership mismatch')
        elif row['kind'] in ('arrival', 'host-agent'):
            reasons.append('Independent Arrival/HostAgent work currently active')
        else:
            raise ValueError('Unknown worker kind')
    if task['state'] == 'Running':
        if len(wrappers) != 1 or len(children) != 1:
            reasons.append('Running task requires exactly one captured wrapper/child pair')
        elif children[0]['parent_pid'] != wrappers[0]['pid'] or children[0]['creation_ticks'] < wrappers[0]['creation_ticks']:
            reasons.append('Captured immediate child linkage/time mismatch')
    elif wrappers or children:
        reasons.append('Stopped task has an owned wrapper/child or scheduler state has not converged')
    for field in ('pause_markers', 'migration_evidence', 'competing_lifecycle_tasks'):
        strings(o[field])
    if o['pause_markers']:
        reasons.append('Pause/eject state requires separate resolution')
    if o['competing_lifecycle_tasks']:
        reasons.append('Competing enabled lifecycle tasks observed')
    evidence = dict(context=c, task=task, task_definition_basis='Current XML snapshot; original R5 receipt does not contain a historical task XML digest', receipt=receipt, authorization_sha256=auth['sha256'], observed_utc=o['observed_utc'], observed_workers=o['workers'])
    if o['migration_evidence']:
        return result('pending_recovery', reasons + ['Existing migration evidence must be authenticated by a recovery-aware provider; names alone do not establish its phase'], phase, evidence)
    return result('refused' if reasons else 'preflight_observations_valid', reasons, phase, evidence)

