"""单进程资源提交与内存版本发布；读取路径不访问数据库。"""

from __future__ import annotations

import asyncio
import inspect
from functools import wraps


class ResourceVersions:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.global_version = 0
        self.failed = False
        self.user_versions: dict[str, int] = {}

    async def read(self, user_id: str) -> tuple[int, int]:
        # 写事务提交与版本发布期间禁止选中旧缓存。
        async with self.lock:
            if self.failed:
                raise RuntimeError("资源失效发布未完成，请重启服务后重试。")
            return self.global_version, self.user_versions.get(user_id, 0)

    def publish(self, user_id: str | None):
        # 同步、无 await：提交结束后即使调用方取消，也不留下未发布窗口。
        if user_id is None:
            self.global_version += 1
        else:
            self.user_versions[user_id] = self.user_versions.get(user_id, 0) + 1


def resource_change(*, user=None, row=None, key=None, scope=None, owner=None, payload=None):
    """显式登记资源写入口。失败保守失效；取消等待受保护事务完成后传播。"""

    def decorate(method):
        signature = inspect.signature(method)

        @wraps(method)
        async def wrapped(self, *args, **kwargs):
            values = dict(signature.bind(self, *args, **kwargs).arguments)
            values.update(values.get("fields", {}))

            async def commit_and_publish():
                async with self.resource_versions.lock:
                    affected = None
                    try:
                        if user:
                            affected = str(values[user])
                        elif row:
                            existing = await getattr(self, row)(str(values[key]))
                            if existing and existing["scope"] == "user":
                                affected = str(
                                    existing.get("owner_user_id") or existing["created_by"]
                                )
                        elif payload:
                            item = values[payload]
                            if item["scope"] == "user":
                                affected = str(item.get("owner_user_id") or item["created_by"])
                        elif scope and values[scope] == "user":
                            affected = str(values[owner])
                        return await method(self, *args, **kwargs)
                    finally:
                        try:
                            self.resource_versions.publish(affected)
                        except BaseException:
                            self.resource_versions.failed = True
                            raise

            task = asyncio.create_task(commit_and_publish())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # 不取消已进入提交边界的写入；调用方取消后仍完成失效发布。
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
                raise

        return wrapped

    return decorate
