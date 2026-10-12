from pathlib import Path
import tomllib

import prometheus_control_center


def test_public_version_matches_packaged_release():
    project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
    expected = tomllib.loads(project.read_text(encoding='utf-8'))['project']['version']
    assert prometheus_control_center.__version__ == expected
