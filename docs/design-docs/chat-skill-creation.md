# 聊天生成 Skill

状态：已实现
日期：2026-10-01

## 背景与目标

用户希望将与 AI 讨论确定的工作流程保存成可复用 Skill。生成、预览与正式发布分开；文件归属和数据库登记由服务端统一处理。admin/owner 创建 global，普通用户创建 user，与既有导入规则一致。

## 方案概览与关键选择

- `.data/skills/shared/skill-creator/SKILL.md` 指导模型提炼用途、输入、步骤、输出和必要参考；不复制整段聊天，不把示例当成固定条件。沿用 db-init 磁盘索引，新内置项默认启用，保留已有个人偏好与停用状态。自定义数据根需部署此目录。
- `tool/skill_install.py` 新增 `prepare_skill_creation(files, enable=true)`，身份仍由 `ToolRuntime` 注入。`core/agent.py` 将其与安装工具一起排除出动态筛选目录，使主模型始终可调用；不进入 PTC。
- `services/skill_creation.py` 只接受顶层 SKILL.md 与 references/ 下安全 .md/.txt 相对路径，最多 32 文件、64000 UTF-8 字节，拒绝绝对路径、穿越、脚本、二进制与无效编码。服务端使用不压缩 ZIP 打包，不读取宿主文件。
- `services/skill_install.py` 复核用户、租户、会话与 Project，调用既有 `SkillImportService.prepare`，来源为 generated，source_ref 为服务端生成的 chat:<conversation_id>。返回完整 generated_files、正文预览、依赖提示和绑定内容摘要的 installation。
- 正式发布仍通过 `confirm_skill_install` 与原 HITL、文件锁、操作日志、ready 状态和快照刷新，不增加 HTTP API 或独立业务表。文件正文是事实来源，数据库保存索引及运营状态。

## 数据与事件流

1. 用户要求从当前聊天创建 Skill，AI 读取 creator 并整理完整文件内容；长对话中已被压缩而无法确定的细节需补问，不能承诺完整读取全部历史。
2. prepare 校验并在数据根 skills/tmp 暂存草稿，无有效 Skill 或正式数据库索引；展示名称、完整文件正文、范围和启用影响。总字节限制使 SKILL.md 不会超出现有正文预览截断阈值。
3. 调用 confirm 后原审批事件暂停执行，只允许 approve/reject。内容和启用选项修改需重新 prepare；同名不自动覆盖，更新走现有管理页面。
4. approve 后重新检查角色、归属、草稿会话、TTL、完整审批清单、同名冲突和暂存内容摘要。global 保存到 skills/shared/<name>，user 保存到 skills/users/<user_id>/<name>。
5. 操作日志记录安装；目录发布后数据库状态改为 ready，并登记 generated 来源、创建者、相对路径、版本、摘要和启用状态。前端沿用安装结果刷新选择器，管理卡片和审批显示聊天生成来源。
6. 启用后下一条消息重新构建有效快照；当前执行不热加载。拒绝不会安装，临时草稿在后续准备时按原 15 分钟 TTL 清理。

## 失败与安全边界

prepare 不进审批，副作用只限受控暂存，不能发布有效内容或执行代码；confirm 沿用 core/hitl.py 固定审批，两者不进 PTC。正文和参考不授予工具权限，不自动执行脚本或安装依赖。不要将模型的内容校验视作安全审计；暂存工具未提供完整凭据扫描，模型应避免生成敏感信息，用户在保存前检查内容。

文件系统与数据库不是同一事务，沿用 SkillOperations 的日志恢复保证可恢复一致性；提交失败返回脱敏错误。若 ready 已提交后响应丢失，应先到管理页核实，不承诺重复提交幂等成功。索引重建沿用原规则，可从目录恢复技能，但清库后不会恢复 generated 来源、原启用偏好或来源会话；个人目录仅在对应用户存在时登记。

当前身份是开发模拟，不是生产认证；LocalShellBackend 不是沙箱。共享部署仍需要可信认证与文件、进程隔离，工具约束不能阻止具有宿主 Shell 权限的主体直接访问文件。

## 运行步骤与预期结果

数据库 source_type CHECK 增加 generated；停止服务，备份后清空开发库（不提供兼容迁移），执行 `uv run melonclaw-db-init`、`scripts/restart.sh`。初始化不会自动清空旧库；只重新执行 create_all 不会替换旧约束。自定义数据根先部署 creator 目录，见 [README](../../README.md#从聊天创建-skill)。无需新增依赖。

配置可用模型后输入「把我们刚才讨论的流程生成一个 Skill 并保存」。预期看到完整生成内容和一次安装审批；允许后在相应全局或个人列表出现，来源为聊天生成；启用后下一条消息可用。

## 验证记录

`tests/test_chat_skill_creation.py` 使用隔离临时目录与仓储替身，覆盖三种角色、启停、完整文件落盘、ready 索引、下一轮真实快照、路径/编码/大小限制、同名竞争、内容篡改、归属和角色复核、过期、提交失败恢复、真实 Deep Agent 图中的 ToolRuntime 注入与 approve/reject，以及 creator 磁盘索引。前端审批测试覆盖聊天生成来源、个人范围和只保存说明。

上游 ToolRuntime 隐藏身份参数方式按 [LangChain 官方工具文档](https://docs.langchain.com/oss/python/langchain/tools) 核实。完整检查结果见 [执行计划](../exec-plans/completed/chat-skill-creation.md)。真实模型生成质量与 PostgreSQL 端到端操作未在本次模拟测试中验证。
