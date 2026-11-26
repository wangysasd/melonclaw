# 管理员归档和删除定时任务

状态：已完成
开始日期：2026-11-26

## 目标

当前身份为 system/admin 时，可以归档或删除任意定时任务。编辑、运行、运行记录和固定会话仍只属于任务所有者。

## 背景

管理员已经可以查看全部任务，但写入仍按所有者过滤，列表上的归档会失败。设计见 [定时任务](../../design-docs/task-center.md)。

## 步骤

- [x] 管理员归档任意任务，效果与所有者归档相同
- [x] 仅管理员可删除任意任务；执行中先请求取消并返回 409
- [x] 删除移除任务和运行记录，保留固定会话与已写入聊天，并清理 Checkpoint
- [x] 任务列表对他人任务只开放归档和删除
- [x] 同步设计、前端说明、验收索引和 README

## 验收标准

- `POST /api/tasks/{id}/archive` 在 system/admin 下可归档他人任务，普通用户仍返回 404
- `POST /api/tasks/{id}/delete` 普通用户返回 403；无执行中运行时返回已删除；执行中返回 409 且已请求取消
- 前端任务列表测试覆盖管理员菜单、删除确认和所有者操作限制

## 决策日志

| 日期 | 决策 | 原因 |
|---|---|---|
| 2026-11-26 | 删除不移除固定会话和聊天记录 | 归档已经承诺保留历史；删除只停止调度并移出任务列表 |
| 2026-11-26 | 执行中的删除先取消并拒绝本次请求，不写入幂等回执 | 执行器仍要提交终态；同一请求不能在运行中和结束后返回两种结果 |

## 风险与依赖

无数据库结构变更。隔离 PostgreSQL 测试依赖 `MELONCLAW_TEST_DATABASE_URL`。

## 验证

- 通过：`frontend` 的 `tests/taskCenter.test.tsx` 7 项；`npm run typecheck`；`eslint` 覆盖 `TaskCenter.tsx` 与 `api/tasks.ts`；`uv run ruff check` 覆盖改动的任务后端文件和 `tests/test_task_postgres.py`。
- 未运行：`tests/test_task_postgres.py` 的管理员归档/删除与原权限用例。当前环境没有 `MELONCLAW_TEST_DATABASE_URL`，未改用业务库。
- 未运行：浏览器里的真实登录、归档和删除。避免改动现有任务数据。
