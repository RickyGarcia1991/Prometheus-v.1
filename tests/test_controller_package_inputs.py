"""Controller builds consume one canonical lifecycle source without executing it."""
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'tools/Prometheus.DriveController'
CANONICAL = ROOT / 'tools/Prometheus.HostAgent'
EXPECTED = {
    'Prometheus-Controller-Common.ps1', 'Prometheus-Drive-Engine.ps1',
    'Prometheus-Eject-Orchestrator.ps1', 'Prometheus-Lifecycle.ps1',
    'Prometheus-Safe-Eject.ps1', 'verify-portable-memory.py',
}


def test_project_declares_canonical_dependency_closure():
    tree = ET.parse(PROJECT / 'Prometheus.DriveController.csproj')
    rows = tree.findall('.//ControllerLifecycleInput')
    assert {Path(row.attrib['Include'].replace('\\', '/')).name for row in rows} == EXPECTED
    for row in rows:
        path = (PROJECT / row.attrib['Include'].replace('\\', '/')).resolve()
        assert path.parent == CANONICAL.resolve()
        assert path.is_file()
    link = tree.find(".//None[@Include='@(ControllerLifecycleInput)']")
    assert link is not None
    assert link.findtext('CopyToOutputDirectory') == 'Always'
    assert link.findtext('CopyToPublishDirectory') == 'Always'


def test_canonical_script_local_dependencies_are_complete():
    for name in EXPECTED:
        text = (CANONICAL / name).read_text(encoding='utf-8-sig')
        dependencies = re.findall(r"Join-Path \$PSScriptRoot '([^']+)'", text)
        assert set(dependencies) <= EXPECTED


def verifier():
    spec = importlib.util.spec_from_file_location('controller_package_inputs', PROJECT / 'verify-package-inputs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_package(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    for name in EXPECTED:
        shutil.copyfile(CANONICAL / name, output / name)
    return output


def test_package_matches_all_canonical_inputs(tmp_path):
    output = fixture_package(tmp_path)
    result = verifier().verify(output)
    assert set(result) == EXPECTED
    assert result == {name: hashlib.sha256((CANONICAL / name).read_bytes()).hexdigest() for name in EXPECTED}


@pytest.mark.parametrize('damage', ['missing', 'changed', 'extra'])
def test_missing_stale_or_extra_scripts_are_rejected(tmp_path, damage):
    output = fixture_package(tmp_path)
    target = output / 'Prometheus-Lifecycle.ps1'
    if damage == 'missing':
        target.unlink()
    elif damage == 'changed':
        target.write_text('# old lifecycle')
    else:
        (output / 'Prometheus-USB-Supervisor.ps1').write_text('# unreviewed extra')
    with pytest.raises(ValueError):
        verifier().verify(output)


@pytest.mark.parametrize('missing', sorted(EXPECTED))
def test_msbuild_refuses_each_missing_canonical_input(tmp_path, missing):
    dotnet = shutil.which('dotnet')
    if not dotnet:
        pytest.skip('A .NET SDK is required for MSBuild validation.')
    controller = tmp_path / 'Prometheus.DriveController'
    canonical = tmp_path / 'Prometheus.HostAgent'
    controller.mkdir()
    canonical.mkdir()
    project = controller / 'Prometheus.DriveController.csproj'
    shutil.copyfile(PROJECT / project.name, project)
    for name in EXPECTED - {missing}:
        shutil.copyfile(CANONICAL / name, canonical / name)
    result = subprocess.run(
        [dotnet, 'msbuild', str(project), '-t:ValidateControllerLifecycleInputs', '-nologo'],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert 'Required canonical controller lifecycle input is missing:' in result.stdout
    assert missing in result.stdout
