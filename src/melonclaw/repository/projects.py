"""Project 仓储。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, select, update

from melonclaw.database.schema import projects
from melonclaw.repository.errors import ProjectNotFoundError
from melonclaw.repository.mappers import (
    _now,
    _project_dict,
)


class ProjectRepositoryMixin:
    async def create_project(
        self,
        user_id: str,
        name: str,
    ) -> dict[str, Any]:
        """创建当前用户的 Project；tenant 只作为用户标签，不参与归属。"""

        project_id = uuid4()
        timestamp = _now()
        values = {
            "id": project_id,
            "user_id": user_id,
            "name": name,
            "workdir_path": f"projects/{project_id}",
            "created_at": timestamp,
            "updated_at": timestamp,
            "status": "active",
            "is_pinned": False,
        }
        async with self.engine.begin() as connection:
            await connection.execute(insert(projects).values(**values))
        return _project_dict(values)

    async def get_project(
        self,
        project_id: UUID,
        user_id: str,
    ) -> dict[str, Any] | None:
        query = select(projects).where(
            and_(
                projects.c.id == project_id,
                projects.c.user_id == user_id,
                projects.c.status == "active",
            )
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(query)).mappings().first()
        return _project_dict(row) if row else None

    async def list_projects(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        query = (
            select(projects)
            .where(
                and_(
                    projects.c.user_id == user_id,
                    projects.c.status == "active",
                )
            )
            .order_by(projects.c.is_pinned.desc(), projects.c.updated_at.desc(), projects.c.id.desc())
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_project_dict(row) for row in rows]

    async def update_project(
        self, project_id: UUID, user_id: str, *, name: str | None = None,
        is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {"updated_at": _now()}
        if name is not None:
            values["name"] = name
        if is_pinned is not None:
            values["is_pinned"] = is_pinned
        if delete:
            values["status"] = "deleted"
        async with self.engine.begin() as connection:
            row = (await connection.execute(
                update(projects)
                .where(and_(projects.c.id == project_id, projects.c.user_id == user_id,
                            projects.c.status == "active"))
                .values(**values).returning(projects)
            )).mappings().first()
        if row is None:
            raise ProjectNotFoundError
        return None if delete else _project_dict(row)
