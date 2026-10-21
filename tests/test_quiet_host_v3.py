"""Isolated source-built quiet host qualification; no installed modes are invoked."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

HOST = Path(__file__).resolve().parents[1] / 'tools/Prometheus.HostAgent'
MODES = {
    'reconnect': 'Prometheus/Prometheus-USB-Reconnect-Supervisor.ps1',
    'recorder': 'Prometheus/Diagnostics/Prometheus-Recovery-Recorder.ps1',
    'watchdog': 'Prometheus/Diagnostics/Prometheus-Recovery-Watchdog.ps1',
    'ssd-backup': 'Prometheus/ssd-memory-backup.ps1',
    'backup': 'Prometheus/host-memory-backup.ps1',
    'volume-watcher': 'Prometheus/Prometheus-Volume-Watcher.ps1',
    'desktop-commander': 'DesktopCommanderStartup/Run-DesktopCommander.ps1',
}


@pytest.fixture(scope='module')
def compiled(tmp_path_factory):
    if os.name != 'nt':
        pytest.skip('Windows .NET Framework process behavior required')
    dotnet = shutil.which('dotnet')
    if not dotnet:
        pytest.skip('Roslyn .NET SDK required')
    rows = subprocess.check_output([dotnet, '--list-sdks'], text=True).splitlines()
    version, directory = rows[-1].split(' [', 1)
    compiler = Path(directory.rstrip(']')) / version / 'Roslyn/bincore/csc.dll'
    framework = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319'
    spec = importlib.util.spec_from_file_location('quiet_build', HOST / 'build-quiet-host.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    root = tmp_path_factory.mktemp('quiet-v3')
    first = builder.build(dotnet, compiler, framework, root / 'first')
    second = builder.build(dotnet, compiler, framework, root / 'second')
    harness_source = root / 'Harness.cs'
    harness_source.write_text(r'''
using System;
class Harness {
 static int Main(string[] args) {
  if(args[0] == "flags") {
   var p=PrometheusQuietHostV3.ChildStart(args[1]);
   return (!p.UseShellExecute && p.CreateNoWindow && p.WindowStyle==System.Diagnostics.ProcessWindowStyle.Hidden &&
    p.RedirectStandardInput && p.RedirectStandardOutput && p.RedirectStandardError &&
    p.FileName.EndsWith(@"WindowsPowerShell\v1.0\powershell.exe") &&
    p.Arguments.Contains("-NoProfile -NonInteractive") && p.Arguments.Contains("-File \""+args[1]+"\"")) ? 0 : 99;
  }
  if(args[0] == "badchild") return PrometheusQuietHostV3.RunChild(new System.Diagnostics.ProcessStartInfo(args[1]) { UseShellExecute=false });
  if(args[0] == "zeroargs") return PrometheusQuietHostV3.Run(new string[0],args[1]);
  if(args[0] == "extraargs") return PrometheusQuietHostV3.Run(new[]{"backup","extra"},args[1]);
  return PrometheusQuietHostV3.Run(new[]{args[0]},args[1]);
 }
}
''', encoding='utf-8')
    harness = root / 'Harness.exe'
    command = [dotnet, str(compiler), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/main:Harness',
               '/out:' + str(harness), *['/reference:' + str(framework / p) for p in ('mscorlib.dll','System.dll','System.Core.dll')],
               str(HOST / 'Prometheus-QuietHost-v3.cs'), str(harness_source)]
    subprocess.run(command, check=True, timeout=60)
    return builder, root, first, second, harness


def run(compiled, mode, root):
    return subprocess.run([str(compiled[-1]), mode, str(root)], capture_output=True, timeout=20).returncode


def test_repeatable_new_identity_and_gui_subsystem(compiled):
    builder, root, first, second, _ = compiled
    assert first['output_sha256'] == second['output_sha256']
    assert first['identity'] == 'Prometheus-QuietHost-v3.exe'
    assert first['signed'] is False and first['installed'] is False
    raw = (root / 'first' / first['identity']).read_bytes()
    pe = struct.unpack_from('<I', raw, 0x3c)[0]
    assert struct.unpack_from('<H', raw, pe + 24 + 68)[0] == 2  # Windows GUI, not console
    assert first['source']['sha256'] == hashlib.sha256((HOST / 'Prometheus-QuietHost-v3.cs').read_bytes()).hexdigest()
    assert subprocess.run([str(root / 'first' / first['identity']), '--probe'], timeout=10).returncode == 0


@pytest.mark.parametrize('mode', ['arbitrary-command', '../backup', 'zeroargs', 'extraargs'])
def test_unknown_or_extra_commands_rejected(compiled, tmp_path, mode):
    assert run(compiled, mode, tmp_path) == 64


@pytest.mark.parametrize('mode', sorted(MODES))
def test_missing_script_returns_66(compiled, tmp_path, mode):
    assert run(compiled, mode, tmp_path) == 66


@pytest.mark.parametrize('marker', ['Prometheus/eject-mode.json', 'DesktopCommanderStartup/runner-paused.request'])
@pytest.mark.parametrize('mode', sorted(MODES))
def test_pause_suppresses_only_work_modes(compiled, tmp_path, marker, mode):
    target = tmp_path / marker
    target.parent.mkdir(parents=True)
    target.write_text('generated')
    assert run(compiled, mode, tmp_path) == (66 if mode in ('reconnect','recorder') else 0)


def test_hidden_process_flags_and_launch_failure(compiled, tmp_path):
    assert run(compiled, 'flags', tmp_path / 'space path.ps1') == 0
    assert run(compiled, 'badchild', tmp_path / 'absent.exe') == 70


@pytest.mark.parametrize('mode', sorted(MODES))
def test_fixed_mode_script_and_child_exit_with_no_console(compiled, tmp_path, mode):
    target = tmp_path / MODES[mode]
    target.parent.mkdir(parents=True)
    target.write_text('''Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ConsoleCheck { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'
if ([ConsoleCheck]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 91 }
1..3000 | ForEach-Object { [Console]::Out.WriteLine('generated output'); [Console]::Error.WriteLine('generated error') }
exit 23
''')
    assert run(compiled, mode, tmp_path) == 23

def test_build_preserves_existing_artifact_and_rejects_missing_input(compiled, tmp_path):
    builder, root, first, _, _ = compiled
    command = first['command']
    framework = Path(next(arg.removeprefix('/reference:') for arg in command if arg.startswith('/reference:'))).parent
    with pytest.raises(ValueError, match='existing artifacts'):
        builder.build(command[0], command[1], framework, root / 'first')
    assert hashlib.sha256((root / 'first' / first['identity']).read_bytes()).hexdigest() == first['output_sha256']
    with pytest.raises(ValueError, match='Missing build input'):
        builder.build(command[0], tmp_path / 'missing-csc.dll', framework, tmp_path / 'missing-output')
    assert not (tmp_path / 'missing-output').exists()
