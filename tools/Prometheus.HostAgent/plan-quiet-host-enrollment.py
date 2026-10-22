"""Read-only fresh enrollment planner. Never installs, verifies trust, or starts tasks.

Its output is a proposed specification with mandatory unresolved activation gates.
Task inventory must explicitly assert completeness; existing selected names cause
refusal rather than guessed ownership or migration.
"""
import argparse
import json
import ntpath
from pathlib import Path

MODES = {
    'Desktop Commander Remote': 'desktop-commander',
    'Prometheus Memory Backup': 'backup',
    'Prometheus SSD Memory Backup': 'ssd-backup',
    'Prometheus Recovery Evidence Recorder': 'recorder',
    'Prometheus Recovery Watchdog': 'watchdog',
    'Prometheus Recovery Watchdog At Logon': 'watchdog',
    'Prometheus USB Reconnect Supervisor': 'reconnect',
    'Prometheus Volume Watcher': 'volume-watcher',
}
REQUIRED = 'Prometheus USB Reconnect Supervisor'
GATES = [
    'Verify trusted signature, complete payload/path/hash coverage and staged-byte readback before all writes.',
    'Review a transactional create-only enrollment implementation with private journal and fault-tested rollback.',
    'Acquire enrollment and existing eject mutexes; recheck all tasks and readable pause state under lock.',
    'Preserve running backup/communication processes and all unrelated tasks; do not call name-only task rewriter.',
    'Verify signed HostAgent selector, arrival, lifecycle and controller dependency closure plus external runner ownership.',
    'Bind Lifecycle resume and planned task to the same root v3 executable; preserve v2 and original files for rollback.',
    'Validate current-user principal and exact settings, register only the planned task, verify before acceptance-controlled start.',
]


def plan(inventory, host_root, scope='reconnect-only'):
    result = {'read_only': True, 'activation_ready': False, 'scope': scope,
              'create_tasks': [], 'change_existing_tasks': [], 'start_tasks': [],
              'blockers': [], 'activation_gates': list(GATES)}
    if scope not in ('reconnect-only', 'optional-all'):
        raise ValueError('Unknown enrollment scope.')
    drive, tail = ntpath.splitdrive(host_root)
    if not (len(drive) == 2 and drive[1] == ':' and tail.startswith(('\\', '/'))) or '..' in host_root.replace('\\','/').split('/'):
        raise ValueError('Host root must be an absolute local Windows path without traversal.')
    result['proposed_host_root'] = ntpath.normpath(host_root)
    if not isinstance(inventory, dict) or inventory.get('complete') is not True or not isinstance(inventory.get('tasks'), list):
        result['blockers'].append('Complete successful task inventory is required; absence cannot be inferred.')
        return result
    tasks = inventory['tasks']
    if any(not isinstance(t, dict) or not isinstance(t.get('name'), str) or not t['name'] or
           not isinstance(t.get('path'), str) or not t['path'].startswith('\\') for t in tasks):
        result['blockers'].append('Malformed inventory; all task identities must be established before planning.')
        return result
    result['observed_task_count'] = len(tasks)
    selected = [t for t in tasks if t['name'].casefold() in {n.casefold() for n in MODES}]
    result['observed_lifecycle_names'] = sorted({t['name'] for t in selected})
    if selected:
        result['blockers'].append('Existing lifecycle task name detected: fresh enrollment refused. Exact ownership/migration review required; no task is rewritten.')
    if scope == 'optional-all':
        result['blockers'].append('Optional schedules/principals/settings are not specified. Do not invent backup, communication or watchdog tasks; legacy volume watcher must not duplicate reconnect supervision.')
    if result['blockers']:
        return result
    result['create_tasks'] = [{
        'name': REQUIRED, 'path': '\\', 'mode': 'reconnect',
        'executable': ntpath.join(result['proposed_host_root'], 'Prometheus-QuietHost-v3.exe'),
        'working_directory': result['proposed_host_root'],
        'principal': 'Current authenticated Windows user; identity must be bound at enrollment',
        'trigger': 'AtLogOn for that same user',
        'settings': {'execution_time_limit': 'PT0S', 'multiple_instances': 'IgnoreNew',
                     'allow_start_on_batteries': True, 'dont_stop_on_batteries': True,
                     'start_when_available': True},
    }]
    result['optional_modes_not_enrolled'] = [n for n in MODES if n != REQUIRED]
    result['required_package_dependencies'] = ['Prometheus-QuietHost-v3.exe', 'Prometheus-USB-Reconnect-Supervisor.ps1',
                                              'Prometheus-Lifecycle.ps1', 'Prometheus-Arrival.ps1', 'Prometheus-Host-Agent.ps1']
    result['external_acceptance_dependencies'] = ['Existing DesktopCommanderStartup runner ownership and pause contract',
                                                 'Signed controller selection and portable interface launcher readiness']
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--host-root', required=True)
    parser.add_argument('--scope', choices=('reconnect-only','optional-all'), default='reconnect-only')
    options = parser.parse_args()
    print(json.dumps(plan(json.loads(options.inventory.read_text(encoding='utf-8-sig')), options.host_root, options.scope), indent=2))
