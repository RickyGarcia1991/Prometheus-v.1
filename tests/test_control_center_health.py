from pathlib import Path
import tempfile

import pytest

from prometheus_control_center.config import PrometheusConfig
from prometheus_control_center.health import run_checks


@pytest.mark.parametrize('directory', ['home', 'archive_dir'])
def test_doctor_preserves_existing_probe_named_file(tmp_path, directory):
    config = PrometheusConfig(tmp_path / 'home', tmp_path / 'archives')
    path = getattr(config, directory)
    path.mkdir()
    sentinel = path / '.prometheus-write-test'
    original = b'Existing owner data must remain unchanged.\x00\xff'
    sentinel.write_bytes(original)

    checks = run_checks(config)

    assert sentinel.is_file(), 'Doctor deleted an existing owner file'
    assert sentinel.read_bytes() == original
    assert all(check.ok for check in checks)
    assert set(path.iterdir()) == {sentinel}, 'Doctor left its own probe behind'


def test_doctor_creates_writable_directories_and_cleans_up(tmp_path):
    config = PrometheusConfig(tmp_path / 'home', tmp_path / 'archives')

    checks = run_checks(config)

    assert all(check.ok for check in checks)
    for path in (config.home, config.archive_dir):
        assert path.is_dir()
        assert list(path.iterdir()) == []


def test_doctor_does_not_overwrite_an_occupied_temporary_candidate(tmp_path, monkeypatch):
    config = PrometheusConfig(tmp_path / 'home', tmp_path / 'archives')
    config.home.mkdir()
    collision = config.home / '.prometheus-write-test-collision'
    collision.write_bytes(b'Keep existing candidate data intact.')
    # Force a collision to verify exclusive creation instead of relying on the
    # low probability that randomly generated temporary names happen to match.
    monkeypatch.setattr(tempfile, '_get_candidate_names', lambda: iter(('collision', 'fresh')))

    checks = run_checks(config)

    assert all(check.ok for check in checks)
    assert collision.read_bytes() == b'Keep existing candidate data intact.'
    assert set(config.home.iterdir()) == {collision}
    assert list(config.archive_dir.iterdir()) == []


@pytest.mark.parametrize('directory', ['home', 'archive_dir'])
def test_doctor_reports_unwritable_directory_and_continues(tmp_path, monkeypatch, directory):
    config = PrometheusConfig(tmp_path / 'home', tmp_path / 'archives')
    denied_path = getattr(config, directory)
    original = tempfile.NamedTemporaryFile

    def create_probe(*args, **kwargs):
        if Path(kwargs['dir']) == denied_path:
            raise PermissionError('Test directory denies temporary file creation')
        return original(*args, **kwargs)

    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', create_probe)

    checks = {check.name: check for check in run_checks(config)}

    assert not checks[directory].ok
    assert 'denies temporary file creation' in checks[directory].detail
    other = 'archive_dir' if directory == 'home' else 'home'
    assert checks[other].ok
    assert list(config.home.iterdir()) == []
    assert list(config.archive_dir.iterdir()) == []


def test_doctor_reports_file_in_place_of_directory_without_changing_it(tmp_path):
    config = PrometheusConfig(tmp_path / 'home', tmp_path / 'archives')
    config.home.write_bytes(b'Owner file at configured directory path')

    checks = {check.name: check for check in run_checks(config)}

    assert not checks['home'].ok
    assert checks['archive_dir'].ok
    assert config.home.read_bytes() == b'Owner file at configured directory path'
