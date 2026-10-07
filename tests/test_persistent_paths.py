"""部署路径独立于代码目录，升级无需搬动用户文件。"""
from pathlib import Path

from melonclaw.core.config import _create_data_root, _create_workspace_root


def test_default_persistent_roots(monkeypatch, tmp_path):
    monkeypatch.delenv("MELONCLAW_DATA_DIR", raising=False)
    monkeypatch.delenv("MELONCLAW_WORKSPACE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _create_data_root() == tmp_path / ".melonclaw" / "data"
    assert _create_workspace_root() == tmp_path / ".melonclaw" / "workspaces"


def test_configured_persistent_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MELONCLAW_DATA_DIR", "~/persistent/resources")
    monkeypatch.setenv("MELONCLAW_WORKSPACE_DIR", "~/persistent/files")
    assert _create_data_root() == tmp_path / "persistent" / "resources"
    assert _create_workspace_root() == tmp_path / "persistent" / "files"


def test_repository_data_root_is_rejected(monkeypatch):
    import pytest

    from melonclaw.core import config

    repository = Path(config.__file__).resolve().parents[3]
    monkeypatch.setenv("MELONCLAW_DATA_DIR", str(repository / ".data"))
    with pytest.raises(ValueError, match="项目代码目录之外"):
        _create_data_root()
    monkeypatch.setenv("MELONCLAW_WORKSPACE_DIR", str(repository / "workspaces"))
    with pytest.raises(ValueError, match="项目代码目录之外"):
        _create_workspace_root()
