"""启动清理 .snapshots/ 的行为：残留快照与半成品目录在启动时全量删除。"""

from pathlib import Path

from melonclaw.services.skill_snapshot import clear_stale_snapshots


def test_clear_stale_snapshots_removes_leftovers(tmp_path: Path) -> None:
    root = tmp_path / "skills" / ".snapshots"
    snapshot = root / "deadbeef"
    snapshot.mkdir(parents=True)
    (snapshot / "global" / "demo").mkdir(parents=True)
    (snapshot / "global" / "demo" / "SKILL.md").write_text("demo", encoding="utf-8")
    partial = root / ".tmp-1234"
    partial.mkdir()
    (partial / "global").mkdir()
    stray = root / "stray-file"
    stray.write_text("x", encoding="utf-8")

    removed = clear_stale_snapshots(tmp_path)

    assert removed == 3
    assert list(root.iterdir()) == []


def test_clear_stale_snapshots_noop_when_missing(tmp_path: Path) -> None:
    assert clear_stale_snapshots(tmp_path) == 0
