"""Project 仓储。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, select, text

from melonclaw.database.constants import DEFAULT_PROJECT_NAME
from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.schema import projects
from melonclaw.repository.mappers import (
    _default_project_id,
    _now,
    _project_dict,
)


class ProjectRepositoryMixin:
    async def ensure_default_project(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """幂等返回用户的“临时会话” Project。"""

        project_id = _default_project_id(user_id)
        timestamp = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, user_id, name, workdir_path, "
                    "created_at, updated_at, status, is_default) "
                    "VALUES (:id, :user_id, :name, :workdir_path, "
                    ":created_at, :updated_at, 'active', TRUE) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "id": project_id,
                    "user_id": user_id,
                    "name": DEFAULT_PROJECT_NAME,
                    "workdir_path": f"projects/{project_id}",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
            result = await connection.execute(
                text(
                    "SELECT id FROM projects "
                    "WHERE user_id = :user_id AND is_default "
                    "ORDER BY id LIMIT 1"
                ),
                {"user_id": user_id},
            )
            selected_id = result.scalar()
        project = (
            await self.get_project(UUID(str(selected_id)), user_id)
            if selected_id
            else None
        )
        if project is None:
            raise DatabaseSchemaError("无法创建或读取用户默认 Project。")
        return project

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
            "is_default": False,
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
            .order_by(projects.c.updated_at.desc(), projects.c.id.desc())
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [_project_dict(row) for row in rows]


