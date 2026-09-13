# 聊天附件上传设计

状态：已实现（首版）

## 背景与目标

在现有 Project、Conversation、Agent 和 SSE 链路上支持图片与普通文件附件。上传结果
需要可重试、可审计；消息只保存附件关系和脱敏摘要，不保存原始文件内容或图片
base64。附件必须经过当前用户、租户上下文、Project 和模型能力的服务端校验。

## 方案概览

采用两阶段协议：浏览器先以 multipart 调用
`POST /api/projects/{project_id}/attachments`，服务端流式计算 SHA-256、做类型/容器
校验并把原文写入 Project 工作区的 `.attachments/<attachment_id>/original/blob`；文本、
PDF 和 Office 文件由后台解析进程生成 `derived/index.md` 与分片。发送消息时只提交
`attachment_ids`，Repository 在同一事务中锁定 Conversation 和附件，写入消息关系，
并把 staged 附件转为 attached。

当前支持 JPG/JPEG/PNG、TXT/MD/CSV/JSON、带文本层 PDF、DOCX/XLSX/PPTX。图片由请求期
`AttachmentHydrationMiddleware` 在模型出站前转换为标准 image content block；文档由
Agent 通过受控虚拟路径按需读取。出站图片经 `services/attachment_images.py` 按文件版本
缓存 base64，并对最长边超过 `MELONCLAW_ATTACHMENT_IMAGE_MAX_EDGE` 的图片等比缩放，
避免一次 Agent run 内重复读盘编码，并控制单次请求的图片体积。历史消息只展示附件摘要和
下载入口，新消息只携带本次上传的附件。

## 关键设计选择

- `chat_attachments` 保存生命周期、解析租约、哈希、派生大小和清理标记；
  `chat_message_attachments` 保存消息关系和顺序。
- Project 配额判断使用 PostgreSQL transaction advisory lock；消息绑定、附件状态迁移
  和 request_id 幂等比较由 Repository 完成，避免服务层先查后写的竞态。
- 图片是否可发送由 `core/model_catalog.py` 的静态 `input_modalities` 决定；不支持图片
  的模型返回 `model_image_unsupported`，不调用 OCR 或图片转文字降级。
- 解析在可终止的独立本机子进程中执行，并受并发、超时和派生字符上限控制；本地
  `LocalShellBackend` 仍不是面向不受信任共享用户的安全沙箱。
- 附件类型与限制以 `GET /api/attachments/capabilities` 为单一来源；请求体只保留一个
  传输层护栏，业务上限来自 `Settings.attachment_max_per_message`。
- 文本附件用增量解码器流式校验 UTF-8，JSON 结构错误在同步校验阶段就返回，不把整个
  文件读进内存。
- 解析失败可由 `POST /api/attachments/{attachment_id}/parse` 重置重试；已在解析中时
  接口幂等返回当前状态。

## 数据与事件流

`Composer` 左下角「+」展开二级目录（1 图片和文件 / 2 技能，悬停展开技能列表）；
选「图片和文件」打开 `AttachmentDialog`（输入区拖拽/粘贴则跳过目录直接进入弹窗）→ 弹窗内拖拽/多选文件，
按 `capabilities` 预校验后上传（XHR 上报进度）→ `AttachmentService` 校验/原子落盘 →
点「确认添加附件」把 staged 附件交给输入区（「取消」删除本次暂存）→ 后台解析 →
Composer 按附件 ID 轮询 `parse_status`（指数退避，超过上限标记超时）→ 发送 JSON
（正文 + 附件 ID）→ Repository 原子绑定 → hydration 或派生文本读取 → 现有 Agent SSE →
`message_started` 和历史消息返回附件摘要。解析失败可用
`POST /api/attachments/{attachment_id}/parse` 重新排队。

`chat_messages.content` 仍只保存用户正文；base64 只存在于当前模型出站请求。上传失败、
解析失败或准备阶段绑定失败不会创建半条消息对；执行阶段失败不会删除已绑定附件。

## 失败与安全边界

- 扩展名、声明 MIME、magic bytes、文件名、大小、图片像素、PDF 页数和 OOXML ZIP 结构
  交叉校验；普通 ZIP、`.ppt`、危险路径、加密/符号链接条目和嵌套容器拒绝上传。
- 跨用户、跨 Project、跨 Conversation 的附件按不存在处理；下载和绑定重新校验服务端
  归属，不能由浏览器提交路径。
- Agent 对 `/.attachments/**` 不可写，对 `/.attachments/**/original/**` 不可读，对
  `/.artifacts/**` 不可写；文档只暴露派生 Markdown 虚拟路径。
- staged 附件由后台任务按 TTL 标记为 expired，物理删除成功后才释放配额；attached
  附件不能通过 DELETE 删除。扫描型/混合 PDF 标记为 `pdf_no_text_layer`，首版不做 OCR。
- 附件下载/预览响应带 `X-Content-Type-Options: nosniff` 和 `Content-Security-Policy:
  default-src 'none'; sandbox`，不允许浏览器按内容嗅探类型。
- 历史消息引用到已删除/过期附件时，hydration 只输出“附件不可用”提示块，不让单条坏
  附件让整轮执行失败；真正的 `attachment_unavailable` 只在发送前绑定阶段抛出。
- 身份来源统一走 `api/identity.py`：默认仍是页面提交的开发模拟用户，部署方可配置
  `MELONCLAW_IDENTITY_HEADER` 改为读取受信任网关注入的请求头；该模块不校验请求头，
  成员关系与归属仍由服务层重新校验。

## 运行步骤与预期结果

```bash
uv sync --locked
uv run melonclaw-db-init
scripts/start.sh
```

初始化后，聊天输入框的“+”可以上传首版支持的文件；文档显示“解析中”期间不能发送，
解析完成后可以发送正文消息或仅附件消息，历史消息可查看附件状态并下载。解析失败的附件
可通过 `POST /api/attachments/{attachment_id}/parse` 重新排队。需要调整限制时，
使用 README 中列出的 `MELONCLAW_ATTACHMENT_*` 环境变量并重启服务。

## 验证记录与当前边界

已通过 `scripts/check.sh`（compileall、33 项后端测试、Ruff、锁文件校验、ESLint、
TypeScript、66 项前端测试）和前端生产构建；附件校验、文本/JSON 前移校验、图片出站缩放
与缓存、能力清单、加号二级目录、附件弹窗与前端预校验、Markdown 派生输出、独立解析进程、
本地存储路径和 API 路由有专项测试/导入检查。真实 PostgreSQL 迁移、真实模型视觉请求、大文件资源压测、
病毒扫描、内存/CPU/打开文件数硬限制、对象存储和生产隔离沙箱仍需在部署环境单独验证。
