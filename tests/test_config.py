from pathlib import Path

from prometheus_control_center.config import PrometheusConfig


def test_default_config_is_rooted_at_cwd(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PROMETHEUS_HOME", raising=False)
    monkeypatch.delenv("PROMETHEUS_ARCHIVE_DIR", raising=False)
    cfg = PrometheusConfig.from_env(tmp_path)
    assert cfg.home == (tmp_path / "runtime").resolve()
    assert cfg.archive_dir == (tmp_path / "archives").resolve()
