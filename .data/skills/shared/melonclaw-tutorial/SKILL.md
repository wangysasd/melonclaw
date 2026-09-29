---
name: melonclaw-tutorial
description: 回答 MelonClaw 系统的使用问题，指导用户配置和使用 Skill、MCP、模型、聊天附件与项目，排查入口找不到、资源不可用和安装失败。用户问“怎么用”“在哪里配置”或使用遇到困难时读取。
---

# MelonClaw 使用指南

先区分用户想了解操作步骤、排查问题，还是要求代为操作。给出当前界面入口、最少必要步骤和成功标志；只有缺少关键事实时才追问。不需要先要求用户学习系统术语。

按问题读取参考文件，不一次加载全部内容：

- Skill 安装、启用、更新、聊天自动安装：[references/skills.md](references/skills.md)。
- MCP 新建、连接、权限、工具可用性：[references/mcp.md](references/mcp.md)。
- 模型供应商、个人 Key、内置／个人模型、默认模型：[references/models.md](references/models.md)。
- 聊天、项目、附件、审批及常见失败：[references/troubleshooting.md](references/troubleshooting.md)。

## 回答边界

- 本指南说明操作方法，不代表用户当前配置。当前 MCP 情况必须调用 `list_mcp_tools`；其他资源没有可用查询工具时，请用户查看相应页面的状态或提供不含密钥的错误提示，不编造已启用或已配置的结果。
- 用户要求安装 Skill 时可直接使用系统工具 `prepare_skill_install` 与 `confirm_skill_install`；执行能力不依赖本教程。展示实际预览，按工具审批完成操作，不改写服务器文件或用 Shell 绕过安装服务。
- 不要求用户把 API Key、Token 或完整认证链接发送到聊天。密钥填写在配置表单，不回显或检查服务器环境变量。
- 当前账号是开发模拟身份，不是生产认证。管理员／owner 可管理共享资源；普通用户管理个人资源；管理员也不能修改别人的个人资源。
- 没有可用模型时 AI 无法回答，包括本教程。用户应先通过模型选择器的“添加自定义模型”进入“拓展 → 模型”，按静态提示配置。
- 若本指南与当前页面不一致，明确指出不确定之处，以实际可见入口和服务端返回为准，不假装已执行操作。
