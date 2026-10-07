"""部署不覆盖用户资源，迁移必须保持隐藏目录和相对路径。"""
import pytest

from melonclaw.storage.deployment import copy_persistent_tree, install_builtin_skills


def test_copy_preserves_hidden_files_and_can_resume(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    original = source / "skills" / ".operations" / "op" / "journal.json"
    original.parent.mkdir(parents=True)
    original.write_text("draft")
    assert copy_persistent_tree(source, target) == 1
    assert copy_persistent_tree(source, target) == 0
    assert (target / original.relative_to(source)).read_text() == "draft"
    assert original.read_text() == "draft"


def test_conflict_is_detected_before_any_copy(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    target.mkdir()
    (source / "a").write_text("new file")
    (source / "z").write_text("source")
    (target / "z").write_text("user change")
    with pytest.raises(ValueError, match="不同内容"):
        copy_persistent_tree(source, target)
    assert not (target / "a").exists()
    assert (target / "z").read_text() == "user change"


def test_overlap_and_symlink_are_rejected(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    with pytest.raises(ValueError, match="重叠"):
        copy_persistent_tree(source, source / "nested")
    (source / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="符号链接"):
        copy_persistent_tree(source, tmp_path / "new")


def test_template_install_preserves_entire_existing_skill(tmp_path):
    source, data = tmp_path / "templates", tmp_path / "data"
    template = source / "example"
    template.mkdir(parents=True)
    (template / "SKILL.md").write_text("builtin")
    assert install_builtin_skills(source, data) == 1
    installed = data / "skills" / "shared" / "example"
    (installed / "SKILL.md").write_text("user change")
    (template / "new.txt").write_text("new version")
    assert install_builtin_skills(source, data) == 0
    assert (installed / "SKILL.md").read_text() == "user change"
    assert not (installed / "new.txt").exists()


def test_failed_template_install_does_not_publish_partial_skill(monkeypatch, tmp_path):
    from melonclaw.storage import deployment

    source, data = tmp_path / "templates", tmp_path / "data"
    template = source / "example"
    template.mkdir(parents=True)
    (template / "SKILL.md").write_text("builtin")

    def fail_copy(source, target):
        target.mkdir(parents=True)
        (target / "partial").write_text("incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(deployment, "copy_persistent_tree", fail_copy)
    with pytest.raises(OSError):
        install_builtin_skills(source, data)
    assert not (data / "skills" / "shared" / "example").exists()
    assert list((data / "skills" / "shared").iterdir()) == []
