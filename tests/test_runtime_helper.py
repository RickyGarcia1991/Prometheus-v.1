"""PowerShell fixtures never query a real API or launch a runtime."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


HELPER = Path(__file__).resolve().parents[1] / 'tools' / 'wait-ollama.ps1'
POWERSHELL = shutil.which('powershell.exe')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell fixture checks')


def run_script(body, **environment):
    # Block side effects even if dot-sourcing accidentally executes the entrypoint.
    script = """
$ErrorActionPreference='Stop'
function Invoke-RestMethod { throw 'Unexpected network call' }
function Get-CimInstance { throw 'Unexpected live process inventory' }
function Start-Process { throw 'Unexpected live process launch' }
. $env:FIXTURE_HELPER
""" + body
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    env = dict(os.environ, FIXTURE_HELPER=str(HELPER), **environment)
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded], capture_output=True, text=True, timeout=15,
        env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_dot_source_only_exposes_functions():
    assert run_script("'loaded' | ConvertTo-Json") == 'loaded'


@pytest.mark.parametrize('command, expected', [
    ('"C:\\Program Files\\Ollama\\ollama.exe" serve', 'server'),
    ('C:\\Ollama\\ollama.exe runner --port 4000', 'runner'),
    ('"C:\\Ollama\\ollama.exe" runner --model "serve"', 'runner'),
    ('', 'unknown'),
    ('C:\\Ollama\\ollama.exe', 'unknown'),
    ('C:\\Ollama\\ollama.exe --unknown serve', 'unknown'),
])
def test_command_classification(command, expected):
    body = r"""$item=[pscustomobject]@{ExecutablePath='C:\Ollama\ollama.exe';CommandLine=$env:FIXTURE_COMMAND}
Get-PrometheusRuntimeProcessKind $item 'C:\Ollama\ollama.exe' | ConvertTo-Json
"""
    assert run_script(body, FIXTURE_COMMAND=command) == expected


def test_other_executable_cannot_claim_server_identity():
    body = r"""$item=[pscustomobject]@{ExecutablePath='C:\Other\ollama.exe';CommandLine='ollama.exe serve'}
Get-PrometheusRuntimeProcessKind $item 'C:\Ollama\ollama.exe' | ConvertTo-Json
"""
    assert run_script(body) == 'other'


def test_missing_executable_identity_prevents_duplicate_launch():
    body = COMMON + """
function Get-PrometheusRuntimeProcesses {
    return [pscustomobject]@{ExecutablePath=$null;CommandLine=$null}
}
""" + END
    result = run_script(body)
    assert result['outcome']['Reason'] == 'existing_process_identity_uncertain'
    assert result['started'] == 0


COMMON = """
$env:OLLAMA_EXE=$env:FIXTURE_HELPER
$script:started=0
$script:tick=0
function Test-PrometheusRuntimeReady { return $false }
function Get-PrometheusRuntimeProcesses { return @() }
function Get-PrometheusRuntimeTick { $script:tick+=0.4; return $script:tick }
function Wait-PrometheusRuntimePoll { }
function Start-PrometheusRuntimeProcess {
    $script:started++
    $process=[pscustomobject]@{Id=1234;HasExited=($env:FIXTURE_EXIT -eq 'yes')}
    $process | Add-Member -MemberType ScriptMethod -Name Refresh -Value { }
    return [pscustomobject]@{Process=$process;ErrorLog=$env:FIXTURE_LOG}
}
"""
END = """
$outcome=Invoke-PrometheusRuntimeStartup -TimeoutSeconds 1
@{ outcome=$outcome; started=$script:started } | ConvertTo-Json -Depth 5
"""


def test_healthy_api_never_enumerates_or_starts():
    body = COMMON + """
function Test-PrometheusRuntimeReady { return $true }
function Get-PrometheusRuntimeProcesses { throw 'Healthy runtime must be reused' }
""" + END
    result = run_script(body)
    assert result['outcome']['Success'] is True
    assert result['started'] == 0


@pytest.mark.parametrize('verb, reason, starts', [
    ('serve', 'existing_server_unready', 0),
    ('', 'existing_process_identity_uncertain', 0),
    ('runner --port 4000', 'readiness_timeout', 1),
])
def test_unready_server_runner_and_unknown_identity(verb, reason, starts):
    body = COMMON + """
function Get-PrometheusRuntimeProcesses {
    $command=if($env:FIXTURE_VERB){'"'+$env:OLLAMA_EXE+'" '+$env:FIXTURE_VERB}else{''}
    return [pscustomobject]@{ExecutablePath=$env:OLLAMA_EXE;CommandLine=$command}
}
""" + END
    result = run_script(body, FIXTURE_VERB=verb)
    assert result['outcome']['Reason'] == reason
    assert result['outcome']['Success'] is False
    assert result['started'] == starts


def test_owned_exit_returns_known_phase_without_log_contents(tmp_path):
    log = tmp_path / 'daemon.log'
    log.write_text('env="SECRET=do-not-return"\nmsg="discovering available GPUs..."\n', encoding='utf-8')
    result = run_script(COMMON + END, FIXTURE_LOG=str(log), FIXTURE_EXIT='yes')
    assert result['outcome']['Reason'] == 'owned_process_exited'
    assert result['outcome']['Phase'] == 'GPU discovery'
    assert 'SECRET' not in json.dumps(result)
    assert result['outcome']['ProcessId'] == 1234


def test_listening_does_not_establish_api_readiness(tmp_path):
    log = tmp_path / 'daemon.log'
    log.write_text('msg="Listening on 127.0.0.1:11434"\n', encoding='utf-8')
    result = run_script(COMMON + END, FIXTURE_LOG=str(log))
    assert result['outcome']['Success'] is False
    assert result['outcome']['Reason'] == 'readiness_timeout'
    assert result['outcome']['Phase'] == 'listening; API not ready'


def test_log_read_is_bounded_to_tail(tmp_path):
    log = tmp_path / 'large.log'
    log.write_text('msg="discovering available GPUs..."\n' + 'private-data\n'*2000, encoding='utf-8')
    result = run_script('Get-PrometheusRuntimePhase $env:FIXTURE_LOG | ConvertTo-Json', FIXTURE_LOG=str(log))
    assert result == 'unknown'


def test_newly_ready_api_is_reused_before_launch():
    body = COMMON + """
$script:calls=0
function Test-PrometheusRuntimeReady { $script:calls++; return $script:calls -gt 1 }
""" + END
    result = run_script(body)
    assert result['outcome']['Success'] is True
    assert result['started'] == 0


def test_native_launch_contract_keeps_process_handle_without_live_launch(tmp_path):
    body = """
$env:LOCALAPPDATA=$env:FIXTURE_DIR
function New-Item { }
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
    if(!$PassThru -or $WindowStyle -ne 'Hidden' -or $ArgumentList -ne 'serve') { throw 'Unsafe launch contract' }
    return [pscustomobject]@{Id=5678}
}
$launched=Start-PrometheusRuntimeProcess 'fixture.exe'
@{ id=$launched.Process.Id; log=$launched.ErrorLog } | ConvertTo-Json
"""
    result = run_script(body, FIXTURE_DIR=str(tmp_path))
    assert result['id'] == 5678
    assert result['log'].endswith('.stderr.log')
    assert list(tmp_path.iterdir()) == []
