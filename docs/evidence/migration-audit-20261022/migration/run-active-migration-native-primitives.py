import argparse, json, subprocess, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--timeout', type=float, default=120)
parser.add_argument('--fault', choices=['none', 'after-other-launch', 'after-task-start-wait'], default='none')
options = parser.parse_args()
if options.timeout <= 0:
    parser.error('--timeout must be positive')
base = Path(__file__).resolve().parent
exe = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
root = Path(tempfile.mkdtemp(prefix='prometheus-active-fixture-'))
command = [str(exe), '-NoProfile', '-File', str(base / 'check-active-migration-native-primitives.ps1'), '-Scratch', str(root), '-Python', sys._base_executable]
report = {'run_id': uuid.uuid4().hex, 'started': datetime.now(timezone.utc).isoformat(), 'scratch': str(root), 'live_prometheus_mutation': False, 'exit': 1, 'fault': options.fault, 'timeout_seconds': options.timeout}

def output(value):
    return value.decode(errors='replace') if isinstance(value, bytes) else value

try:
    result = subprocess.run(command + ['-Fault', options.fault], capture_output=True, text=True, timeout=options.timeout)
    report.update(exit=result.returncode, stdout=result.stdout, stderr=result.stderr, timed_out=False)
except subprocess.TimeoutExpired as error:
    report.update(stdout=output(error.stdout), stderr=output(error.stderr), timed_out=True, failure=f'Native fixture exceeded {options.timeout} seconds')
except Exception as error:
    report['failure'] = repr(error)
finally:
    # Separate recovery process also handles launch-to-identity-capture interruption.
    # Never remove scratch evidence; every attempt has an immutable unique report.
    if (root / 'ownership.json').exists():
        try:
            recovery = subprocess.run(command + ['-CleanupOnly'], capture_output=True, text=True, timeout=60)
            report['recovery'] = {'exit': recovery.returncode, 'stdout': recovery.stdout, 'stderr': recovery.stderr}
            if recovery.returncode:
                report['exit'] = 1
        except Exception as error:
            report['recovery'] = {'failure': repr(error)}
            report['exit'] = 1
    else:
        report['recovery'] = {'failure': 'No ownership record; inspect retained scratch before concluding cleanup'}
        report['exit'] = 1
    report['finished'] = datetime.now(timezone.utc).isoformat()
    serialized = json.dumps(report, indent=2)
    (base / ('active-migration-native-primitives-' + report['run_id'] + '.json')).write_text(serialized)
    (base / 'active-migration-native-primitives-results.json').write_text(serialized)
    print(serialized)
sys.exit(report['exit'])

