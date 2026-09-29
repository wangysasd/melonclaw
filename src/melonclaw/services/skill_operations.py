"""同一数据根的 Skill 文件操作日志和恢复。要求 POSIX 文件锁/原子重命名。"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4


class SkillOperationError(ValueError):
    status_code = 409


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".new")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class SkillOperations:
    def __init__(self, data_root: Path):
        self.root = data_root / "skills"
        self.journals = self.root / ".operations"

    @asynccontextmanager
    async def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".operations.lock").open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def target(self, storage_path: str) -> Path:
        relative = Path(storage_path)
        parts = relative.parts
        valid_layout = ((len(parts) == 2 and parts[0] == "shared") or
                        (len(parts) == 3 and parts[0] == "users"))
        if relative.is_absolute() or not valid_layout or any(part in {".", ".."} for part in parts):
            raise SkillOperationError("技能存储路径不合法。")
        target = self.root / relative
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise SkillOperationError("技能存储路径不能包含符号链接。")
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise SkillOperationError("技能存储路径越界。")
        return target

    async def recover(self, storage) -> list[str]:
        """调用者持锁。数据库已提交就清理日志，否则恢复旧内容与运营状态。"""
        recovered = []
        if not self.journals.exists():
            return recovered
        for journal in sorted(self.journals.iterdir()):
            manifest = journal / "operation.json"
            if not manifest.is_file():
                # 日志尚未发布时没有数据库/目标目录变更。
                shutil.rmtree(journal)
                continue
            operation = json.loads(manifest.read_text())
            await self._recover_one(storage, journal, operation)
            recovered.append(operation["id"])
        return recovered

    async def _recover_one(self, storage, journal: Path, operation: dict):
        row = await storage.get_skill_row(operation["id"])
        target = self.target(operation["storage_path"])
        backup, staged = journal / "old", journal / "new"
        if operation["kind"] == "delete":
            if row is not None and backup.exists():
                if target.exists():
                    raise SkillOperationError("恢复删除时发现目录冲突，需管理员处理。")
                backup.rename(target)
        else:
            committed = (
                row is not None
                and row["status"] == "ready"
                and row["content_hash"] == operation["new"]["content_hash"]
                and row["version"] == operation["new"]["version"]
            )
            if not committed:
                if backup.exists():
                    if target.exists():
                        shutil.rmtree(target)
                    backup.rename(target)
                elif not operation["had_target"] and not staged.exists() and target.exists():
                    shutil.rmtree(target)
                if operation["old"] is None:
                    if row is not None:
                        await storage.delete_skill_row(row["id"])
                elif row is not None:
                    await storage.update_skill_row(row["id"], **operation["old"])
        shutil.rmtree(journal)

    async def install(self, storage, source: Path, *, row: dict | None, fields: dict, enabled: bool = False) -> dict:
        """调用者持锁且完成版本校验。启停/归属/ID 更新时保持不变。"""
        target = self.target(fields["storage_path"])
        if row is None and target.exists():
            raise SkillOperationError("技能目录已存在，请先检查索引。")
        skill_id = str(row["id"]) if row else str(uuid4())
        journal = self.journals / uuid4().hex
        journal.mkdir(parents=True)
        shutil.copytree(source, journal / "new")
        new = {
            key: fields[key] for key in ("content_hash", "source_type", "source_url", "source_ref")
        }
        new.update(version=row["version"] + 1 if row else 1, status="ready")
        if row is None:
            new["enabled"] = enabled
        old = {key: row[key] for key in new} if row else None
        operation = {
            "kind": "update" if row else "install",
            "id": skill_id,
            "storage_path": fields["storage_path"],
            "had_target": target.exists(),
            "old": old,
            "new": new,
        }
        write_json(journal / "operation.json", operation)
        try:
            if row is None:
                await storage.create_skill_row(
                    **fields, skill_id=UUID(skill_id), status="installing", enabled=False
                )
            else:
                await storage.update_skill_row(row["id"], status="updating")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.rename(journal / "old")
            (journal / "new").rename(target)
            await storage.update_skill_row(UUID(skill_id), **new)
        except Exception:
            await self._recover_one(storage, journal, operation)
            raise
        shutil.rmtree(journal)
        return {"id": skill_id, **new}

    async def delete(self, storage, row: dict) -> None:
        """先隔离目录再删除行。残留垃圾永远位于扫描根之外。"""
        target = self.target(row["storage_path"])
        journal = self.journals / uuid4().hex
        journal.mkdir(parents=True)
        operation = {"kind": "delete", "id": str(row["id"]), "storage_path": row["storage_path"]}
        write_json(journal / "operation.json", operation)
        try:
            if target.exists():
                target.rename(journal / "old")
            await storage.delete_skill_row(row["id"])
        except Exception:
            await self._recover_one(storage, journal, operation)
            raise
        shutil.rmtree(journal)
