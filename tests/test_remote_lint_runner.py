import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from prometheus_assistant import remote_lint_runner as runner


@pytest.fixture
def tool(tmp_path):
    path = tmp_path / 'ruff'
    path.write_bytes(b'\x7fELF' + b'pinned test tool')
    path.chmod(0o700)
    return dict(tool_path=path, tool_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                tool_version='0.15.0')


def files():
    return [dict(path='pkg/example.py', content='import os\nprint(missing)\n')]


def diagnostic(**changed):
    row = dict(filename='pkg/example.py', code='F821', message='Undefined name `missing`',
               location=dict(row=2, column=7), end_location=dict(row=2, column=14),
               fix=None, noqa_row=2, url='https://docs.astral.sh/ruff/rules/undefined-name/')
    row.update(changed)
    return row


class ToolProcess:
    def __init__(self, rows=(), exit_code=None, effect=None):
        self.rows = list(rows)
        self.exit_code = (1 if rows else 0) if exit_code is None else exit_code
        self.effect = effect
        self.calls = []

    def __call__(self, argv, *, cwd, env, timeout, tool_fd, tool_sha256, stdin):
        self.calls.append((argv, cwd, env, timeout))
        assert tool_fd >= 0 and len(tool_sha256) == 64
        if argv[1:] == ['--version']:
            assert stdin == b''
            return 0, b'ruff 0.15.0\n', b''
        assert stdin == files()[0]['content'].encode()
        assert not list(Path(cwd).iterdir())
        if self.effect:
            self.effect(Path(cwd).parent/'source')
        return self.exit_code, json.dumps(self.rows).encode(), b''


def test_findings_are_not_tool_failure_and_source_is_exact(tool):
    fake = ToolProcess([diagnostic()])
    got = runner.execute_linux_lint(files(), **tool, executor=fake)
    assert got['state'] == 'completed' and got['status'] == 'findings'
    assert got['source_unchanged'] is True
    assert got['diagnostics'][0] == dict(path='pkg/example.py', code='F821',
        message='Undefined name `missing`', line=2, column=7, end_line=2, end_column=14)
    assert got['files'] == [dict(path='pkg/example.py',
        sha256=hashlib.sha256(files()[0]['content'].encode()).hexdigest())]
    assert len(fake.calls) == 2
    command, cwd, env, timeout = fake.calls[1]
    assert '--no-fix' in command and '--no-fix-only' in command
    assert '--isolated' in command and '--no-cache' in command
    assert command[-3:] == ['--stdin-filename', 'pkg/example.py', '-']
    assert env['RAYON_NUM_THREADS'] == '1'
    assert not Path(cwd).exists()


def test_clean_result_is_separate_and_parent_environment_not_inherited(tool, monkeypatch):
    monkeypatch.setenv('RUFF_OUTPUT_FORMAT', 'sarif')
    monkeypatch.setenv('SECRET_PARENT_VALUE', 'not-for-child')
    fake = ToolProcess()
    got = runner.execute_linux_lint(files(), **tool, executor=fake)
    assert got['status'] == 'passed' and got['diagnostics'] == []
    env = fake.calls[0][2]
    assert 'RUFF_OUTPUT_FORMAT' not in env and 'SECRET_PARENT_VALUE' not in env


@pytest.mark.parametrize('path', ['../x.py', '/x.py', 'C:/x.py', 'a\\b.py',
    '.hidden/x.py', 'venv/x.py', 'node_modules/x.py', 'CON.py', 'x.py/other.py',
    'x.txt', '@args.py', 'a//b.py', 'x./y.py'])
def test_unsafe_snapshot_paths_never_launch(tool, path):
    fake = ToolProcess()
    with pytest.raises(ValueError):
        runner.execute_linux_lint([dict(path=path, content='')], **tool, executor=fake)
    assert fake.calls == []


@pytest.mark.parametrize('rows', [[], [dict(path='a.py', content='x' * 256001)],
    [dict(path='A.py', content=''), dict(path='a.py', content='')],
    [dict(path='A/x.py', content=''), dict(path='a/y.py', content='')],
    [dict(path='x.py', content='\ud800')],
    [dict(path='x.py', content='', flags=['--fix'])]])
def test_invalid_snapshot_never_launches(tool, rows):
    fake = ToolProcess()
    with pytest.raises(ValueError):
        runner.execute_linux_lint(rows, **tool, executor=fake)
    assert fake.calls == []


@pytest.mark.parametrize('changed', [dict(profile='arbitrary'), dict(timeout_seconds=True),
    dict(timeout_seconds=31), dict(tool_sha256='0' * 63), dict(tool_version='0.15.0 --fix')])
def test_only_fixed_profile_and_bounded_parameters(tool, changed):
    fake = ToolProcess()
    with pytest.raises(ValueError):
        runner.execute_linux_lint(files(), **{**tool, **changed}, executor=fake)
    assert fake.calls == []


def test_wrong_tool_bytes_fail_before_execution(tool):
    fake = ToolProcess()
    Path(tool['tool_path']).write_bytes(b'\x7fELFreplaced')
    got = runner.execute_linux_lint(files(), **tool, executor=fake)
    assert got['status'] == 'tool_failed' and got['reason'] == 'tool_identity_mismatch'
    assert fake.calls == []


def test_non_elf_tool_is_not_executed(tool):
    fake = ToolProcess()
    Path(tool['tool_path']).write_bytes(b'#!/bin/sh\necho fake')
    tool['tool_sha256'] = hashlib.sha256(Path(tool['tool_path']).read_bytes()).hexdigest()
    got = runner.execute_linux_lint(files(), **tool, executor=fake)
    assert got['status'] == 'tool_failed' and not fake.calls


def test_version_mismatch_does_not_lint(tool):
    calls = []
    def wrong_version(*args, **kwargs):
        calls.append(args)
        return 0, b'ruff 0.14.0\n', b''
    got = runner.execute_linux_lint(files(), **tool, executor=wrong_version)
    assert got['reason'] == 'tool_version_mismatch' and len(calls) == 1


@pytest.mark.parametrize('exit_code,rows', [(0, [diagnostic()]), (1, []), (2, []), (-9, [])])
def test_inconsistent_or_error_exit_is_not_a_pass(tool, exit_code, rows):
    got = runner.execute_linux_lint(files(), **tool,
        executor=ToolProcess(rows, exit_code=exit_code))
    assert got['status'] == 'tool_failed' and got['diagnostics'] == []


@pytest.mark.parametrize('row', [diagnostic(filename='../other.py'),
    diagnostic(filename='other.py'), diagnostic(code='I001'),
    diagnostic(location=dict(row=True, column=1)), diagnostic(location=dict(row=99, column=1)),
    diagnostic(end_location=dict(row=1, column=1)), diagnostic(message='x' * 161),
    diagnostic(message='\u00e9' * 81), diagnostic(message='control\x7f'),
    diagnostic(message='control\x85'), diagnostic(message='control\x9f')])
def test_unbound_or_invalid_diagnostics_fail_closed(tool, row):
    got = runner.execute_linux_lint(files(), **tool, executor=ToolProcess([row]))
    assert got['status'] == 'tool_failed' and got['diagnostics'] == []


def test_absolute_tool_filename_is_normalized_inside_snapshot(tool):
    class AbsoluteTool(ToolProcess):
        def __call__(self, argv, **kwargs):
            if '--version' not in argv:
                self.rows = [diagnostic(filename=str(Path(kwargs['cwd'])/'pkg/example.py'))]
                self.exit_code = 1
            return super().__call__(argv, **kwargs)
    got = runner.execute_linux_lint(files(), **tool, executor=AbsoluteTool())
    assert got['diagnostics'][0]['path'] == 'pkg/example.py'


@pytest.mark.parametrize('kind', ['modify', 'extra', 'remove'])
def test_changed_snapshot_is_never_accepted(tool, kind):
    def mutate(root):
        path = root/'pkg/example.py'
        path.chmod(0o600)
        if kind == 'modify':
            path.write_text('changed')
        elif kind == 'extra':
            (root/'other.py').write_text('')
        else:
            path.unlink()
    got = runner.execute_linux_lint(files(), **tool, executor=ToolProcess(effect=mutate))
    assert got['status'] == 'tool_failed' and got['source_unchanged'] is False
    assert got['reason'] == 'source_changed' and got['diagnostics'] == []


@pytest.mark.parametrize('output', [b'not json', b'{}', b'[]' + b' ' * 65536,
    b'[{"code":NaN}]', b'[{"filename":"x","filename":"y"}]'],
    ids=['malformed', 'object', 'oversize', 'nonfinite', 'duplicate-key'])
def test_bad_or_oversize_output_fails(tool, output):
    fake = ToolProcess()
    def bad(argv, **kwargs):
        if '--version' in argv:
            return fake(argv, **kwargs)
        return 0, output, b''
    got = runner.execute_linux_lint(files(), **tool, executor=bad)
    assert got['status'] == 'tool_failed' and got['diagnostics'] == []


def test_timeout_is_tool_failure_and_staging_is_removed(tool):
    fake = ToolProcess()
    def timeout(argv, **kwargs):
        if '--version' in argv:
            return fake(argv, **kwargs)
        raise subprocess.TimeoutExpired(argv, 1)
    got = runner.execute_linux_lint(files(), **tool, executor=timeout)
    assert got['reason'] == 'timeout' and got['source_unchanged'] is True
    assert not Path(fake.calls[0][1]).exists()


def test_production_does_not_run_on_windows(tool):
    if sys.platform == 'linux':
        pytest.skip('Non-Linux rejection')
    with pytest.raises(RuntimeError, match='Linux'):
        runner.execute_linux_lint(files(), **tool)


def test_multifile_stdin_is_exact_and_diagnostics_sorted(tool):
    samples = [dict(path='z.py', content='\u00e9 = missing\n'),
               dict(path='a.py', content='print(nope)\n')]
    received = []
    def lint(argv, *, stdin, **kwargs):
        if '--version' in argv:
            return 0, b'ruff 0.15.0\n', b''
        filename = argv[-2]
        received.append((filename, stdin))
        if filename == 'z.py':
            row = diagnostic(filename=filename, location=dict(row=1, column=5),
                             end_location=dict(row=1, column=12))
        else:
            row = diagnostic(filename=filename, location=dict(row=1, column=7),
                             end_location=dict(row=1, column=11))
        return 1, json.dumps([row]).encode(), b''
    got = runner.execute_linux_lint(samples, **tool, executor=lint)
    assert received == [(row['path'], row['content'].encode()) for row in samples]
    assert [row['path'] for row in got['diagnostics']] == ['a.py', 'z.py']
    assert got['source_unchanged'] is True


def test_all_calls_share_one_wall_deadline(tool, monkeypatch):
    tick = [100.0]
    monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    timeouts = []
    def slow(argv, *, timeout, **kwargs):
        timeouts.append(timeout)
        if '--version' in argv:
            tick[0] += 9
            return 0, b'ruff 0.15.0\n', b''
        tick[0] += 2
        return 0, b'[]', b''
    got = runner.execute_linux_lint(files(), **tool, executor=slow, timeout_seconds=10)
    assert got['reason'] == 'timeout' and timeouts == [10, 1]
    assert got['diagnostics'] == []


def test_tool_change_after_version_is_rejected_before_source_send(tool):
    calls = []
    def replace(argv, **kwargs):
        calls.append(argv)
        Path(tool['tool_path']).write_bytes(b'\x7fELFmutated')
        return 0, b'ruff 0.15.0\n', b''
    got = runner.execute_linux_lint(files(), **tool, executor=replace)
    assert got['reason'] == 'tool_identity_mismatch' and len(calls) == 1


def test_message_bound_counts_utf8_bytes(tool):
    got = runner.execute_linux_lint(files(), **tool,
        executor=ToolProcess([diagnostic(message='\u00e9' * 80)]))
    assert got['status'] == 'findings'


def test_diagnostic_overflow_is_failure_not_truncated_success(tool):
    got = runner.execute_linux_lint(files(), **tool,
        executor=ToolProcess([diagnostic()] * 17))
    assert got['status'] == 'tool_failed' and got['diagnostics'] == []


def test_duplicate_diagnostics_are_not_accepted(tool):
    got = runner.execute_linux_lint(files(), **tool,
        executor=ToolProcess([diagnostic(), diagnostic()]))
    assert got['reason'] == 'duplicate_diagnostics' and got['diagnostics'] == []


def test_crlf_position_removes_only_one_carriage_return(tool):
    samples = [dict(path='a.py', content='x\r\r\n')]
    def lint(argv, **kwargs):
        if '--version' in argv:
            return 0, b'ruff 0.15.0\n', b''
        row = diagnostic(filename='a.py', location=dict(row=1, column=1),
            end_location=dict(row=1, column=3))
        return 1, json.dumps([row]).encode(), b''
    got = runner.execute_linux_lint(samples, **tool, executor=lint)
    assert got['status'] == 'findings' and got['diagnostics'][0]['end_column'] == 3


def test_aggregate_output_bound_covers_all_files(tool):
    samples = [dict(path='a.py', content=''), dict(path='b.py', content='')]
    def verbose(argv, **kwargs):
        if '--version' in argv:
            return 0, b'ruff 0.15.0\n', b''
        return 0, b'[]' + b' ' * 33000, b''
    got = runner.execute_linux_lint(samples, **tool, executor=verbose)
    assert got['reason'] == 'output_limit' and got['diagnostics'] == []


def test_stderr_is_failure_not_hidden_clean_result(tool):
    def warned(argv, **kwargs):
        if '--version' in argv:
            return 0, b'ruff 0.15.0\n', b''
        return 0, b'[]', b'could not inspect input'
    got = runner.execute_linux_lint(files(), **tool, executor=warned)
    assert got['reason'] == 'tool_exit'


def test_no_python_source_execution_even_in_test_seam(tool, tmp_path):
    marker = tmp_path/'never-executed'
    sample = [dict(path='x.py', content=f'import unknown_module\nopen({str(marker)!r}, "w")\n')]
    def read_only(argv, *, stdin, cwd, **kwargs):
        if '--version' in argv:
            return 0, b'ruff 0.15.0\n', b''
        assert stdin == sample[0]['content'].encode()
        assert not list(Path(cwd).iterdir())
        return 0, b'[]', b''
    assert runner.execute_linux_lint(sample, **tool, executor=read_only)['status'] == 'passed'
    assert not marker.exists()


def test_input_total_bytes_and_file_count_bounds(tool):
    fake = ToolProcess()
    for samples in ([dict(path=f'f{i}.py', content='') for i in range(9)],
                    [dict(path=f'f{i}.py', content='x' * 180000) for i in range(3)]):
        with pytest.raises(ValueError):
            runner.execute_linux_lint(samples, **tool, executor=fake)
    assert fake.calls == []


def test_oversized_directory_tree_is_rejected_before_staging(tool):
    samples = [dict(path=f'root{i}/' + '/'.join(['p'] * 20) + '/x.py', content='')
               for i in range(7)]
    fake = ToolProcess()
    with pytest.raises(ValueError, match='entry bound'):
        runner.execute_linux_lint(samples, **tool, executor=fake)
    assert fake.calls == []


def test_invoke_exception_preserves_source_failure_priority(tool):
    fake = ToolProcess()
    def failed(argv, **kwargs):
        if '--version' in argv:
            return fake(argv, **kwargs)
        source = Path(kwargs['cwd']).parent/'source/pkg/example.py'
        source.chmod(0o600)
        source.write_text('different')
        raise OSError('private tool path must not escape')
    got = runner.execute_linux_lint(files(), **tool, executor=failed)
    assert got['reason'] == 'source_changed' and got['source_unchanged'] is False
    assert 'private tool path' not in str(got)


def test_interruption_does_not_leave_staged_files(tool):
    fake = ToolProcess()
    def interrupted(argv, **kwargs):
        if '--version' in argv:
            return fake(argv, **kwargs)
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        runner.execute_linux_lint(files(), **tool, executor=interrupted)
    assert not Path(fake.calls[0][1]).exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux native limits require Linux.')
def test_real_installed_ruff_if_available(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('An unprivileged worker is required for process limits.')
    executable = shutil.which('ruff')
    if executable is None:
        pytest.skip('Ruff is not installed; no download in this test.')
    path = Path(executable).resolve()
    if path.read_bytes()[:4] != b'\x7fELF':
        pytest.skip('A directly pinned ELF Ruff executable is required.')
    version = subprocess.check_output([str(path), '--version'], timeout=5).decode().strip()[5:]
    marker = tmp_path/'never-executed'
    sample = [dict(path='sample.py', content=f'open({str(marker)!r}, "w").write("bad")\nprint(undefined_name)\n')]
    got = runner.execute_linux_lint(sample, tool_path=path,
        tool_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), tool_version=version)
    assert got['status'] == 'findings' and got['source_unchanged']
    assert any(row['code'] == 'F821' for row in got['diagnostics'])
    assert not marker.exists()
