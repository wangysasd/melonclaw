# 用户 Skill 与 MCP 资源管理

状态：已实现。核对日期：2026-10-17。

## 当前资源边界

Skill 与 MCP 均有 global/user 两级归属。管理员管理共享资源，普通用户管理自己的私有资源；角色、用户、范围和归属由服务端校验。前端的启停、查看、安装与更新不赋予 Agent 额外权限。

| 资源 | 权威设计 | 实现入口 |
|---|---|---|
| Skill 身份、有效状态、内容更新、持久草稿与恢复 | [Skill 生命周期](skill-lifecycle.md) | services/skill_state.py、skill_import.py、skill_operations.py、skill_snapshot.py |
| 聊天 ZIP/GitHub 安装 | [聊天安装 Skill](chat-skill-install.md) | services/skill_install.py、tool/skill_install.py |
| 聊天生成 Skill | [聊天生成 Skill](chat-skill-creation.md) | services/skill_creation.py、tool/skill_install.py |
| MCP 配置、同名来源、凭据、白名单与发现 | [MCP 两层配置](mcp-two-layer.md) | services/mcp.py、mcp_management.py、mcp_discovery.py |
| 聊天 MCP 草稿与审批安装 | [聊天安装 MCP](chat-mcp-install.md) | services/mcp_install.py、tool/mcp_install.py |

## Skill 文件与索引

正文存于外部数据根的 skills/shared 或 skills/users，数据库保存资源身份、版本、内容摘要、状态和偏好。仓库 .data/skills/shared 只分发内置模板，升级不得覆盖用户运行时内容，见[持久文件](persistent-user-files.md)。

services/skill_index.py 重建索引只补缺失记录，不覆盖已有归属、启停或个人偏好；磁盘缺失、文件异常和无有效用户归属分别诊断，不把扫描不到当作自动删除授权。管理员可以通过恢复与检查处理操作日志和索引。

管理页、Picker、显式选择与 Agent 统一消费 skill_state 的有效状态。共享项同时受全员开关和个人偏好约束；有效私有项才遮蔽同名共享项。更新保留资源 ID、归属和个人偏好，内容与数据库提交使用文件锁及可恢复操作日志。

运行时使用只读内容快照；缓存依据实际资源 ID、版本和内容摘要，不以全局更新时间代替个人有效目录。SkillRefreshMiddleware 每轮刷新构建时快照的元数据。持久草稿按 manifest 的 expires_at 清理，不能按进程内草稿表判断存活。

## MCP 与授权

mcp.json 只在初始化时单向补录种子，运行时读取数据库。个人同名记录优先于共享记录，停用或失败不自动回退；凭据和个人使用偏好分开。配置修改下一条消息生效，已有运行保留已构建工具。

已启用且白名单允许的 MCP 调用免逐次审批；聊天安装和测试仍需审批。Skill 安装确认同样走固定审批入口。MCP、Shell 和文件写入不因资源管理或工具池选择进入 PTC。

Web 统一使用 Cookie 会话，业务 user_id 仅作当前身份断言；服务层再次验证租户、会话及项目归属。profile=dev 的显式切换与 LocalShellBackend 非沙箱限制保持原样。

## 验证

现有覆盖包括 test_skill_lifecycle.py、test_skill_index.py、test_skill_snapshot.py、test_skill_refresh.py、test_mcp_management.py、test_mcp_discovery.py 和对应前端资源测试。数据库场景只使用专用测试连接和随机隔离 schema；外部 Skill、真实 MCP、真实供应商与生产部署需要分别验证。
