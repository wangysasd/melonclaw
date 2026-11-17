# 聊天附件上传设计

状态为已实现。 [文件类型、识别策略和限额](chat-attachment-processing.md)

## 背景与目标

在现有 Project、Conversation、Agent 和 SSE 链路上支持图片与普通文件附件。上传结果
需要可重试、可审计；消息只保存附件关系和脱敏摘要，不保存原始文件内容或图片
base64。附件必须经过当前用户唯一有效租户归属、工作区和模型能力的服务端校验。

## 方案概览

浏览器以 multipart 调用 Project 的 `POST /api/projects/{project_id}/attachments` 或普通会话的
`POST /api/conversations/{conversation_id}/attachments`。服务端流式计算 SHA-256、校验内容，
把原件写入当前工作区的 `.attachments/<attachment_id>/original/blob`。需要 OCR 的文件在上传请求中调用 DeepSeek。
本地文本与表格由附件解析子进程读取。派生正文统一存为 `derived/index.md` 与分片。
发送消息时提交 `attachment_ids`，Repository 在同一事务中锁定 Conversation 和附件，写入消息关系，
并把 staged 附件转为 attached。

视觉模型请求由 `AttachmentHydrationMiddleware` 生成标准图片块。出站图片由
`services/attachment_images.py` 按附件版本缓存，最长边超过
`MELONCLAW_ATTACHMENT_IMAGE_MAX_EDGE` 时等比缩放。
文本模型读取图片的 OCR 正文，文档正文也经受控虚拟路径提供。历史界面展示附件摘要和下载入口。

## 关键设计选择

- `chat_attachments` 保存生命周期、解析租约、哈希、派生大小和清理标记；
  `chat_message_attachments` 保存消息关系和顺序。
- 附件物理路径由当前工作区和 `attachment_id` 唯一推导，不在数据库重复保存
  `storage_key`。
- 每个工作区的配额判断使用 PostgreSQL transaction advisory lock；消息绑定、附件状态迁移
  和 request_id 幂等比较由 Repository 完成，避免服务层先查后写的竞态。
- 实际解析的聊天模型通过 `input_modalities` 声明图片能力。图片可以在不同消息中使用图像或 OCR 正文，
  本次绑定使用的 OCR 图片 ID 记录在原有消息元数据中。
- 本地解析在可终止的子进程中执行。OCR 在上传或显式解析请求中等待 DeepSeek。两条路径都受并发、
  超时和派生字符上限控制。
- 附件类型与限制以 `GET /api/attachments/capabilities` 为单一来源；请求体只保留一个
  传输层护栏，业务上限来自 `Settings.attachment_max_per_message`。
- 文本附件用增量解码器流式校验 UTF-8，JSON 结构错误在同步校验阶段就返回，不把整个
  文件读进内存。
- 解析失败可用原附件 ID 调用 `POST /api/attachments/{attachment_id}/parse` 重试。该接口也用于切换到文本模型后补识别图片。
  并发解析返回已领取的处理状态。

## 模型能力与请求副本

`core/model_catalog.py` 读取数据库中的 `input_modalities` 声明，`core/chat_model.py` 按声明注入模型 profile。
发送准备复核实际模型能力。`AttachmentHydrationMiddleware` 在请求副本中加入图片块或已绑定的派生正文路径。
业务消息和图历史只保存正文与附件引用。

当前控制点见 core/chat_model.py、services/attachment_images.py、middleware/attachment_hydration.py 与 backend/factory.py；tests/test_attachments.py、tests/test_result_hydration.py、tests/test_model_configs.py 覆盖相应边界。框架版本升级时仍须复核这些行为，旧依赖源码行号不能作为当前保证。

## 数据与事件流

在 `Composer` 的「+」里选「图片和文件」打开 `AttachmentDialog`。输入区拖拽和粘贴也进入同一弹窗。
弹窗读取 `capabilities` 预检后开始上传，XHR 返回传输进度。文件字节送达后，需 OCR 的条目等待识别结果。
点「确认」将 staged 附件交给输入区，点「取消」删除本次已登记的暂存。输入区按附件 ID 轮询未完成的
解析状态。发送前按本次模型准备图片文字，服务端验证附件并在事务中绑定。`message_started` 和历史消息返回附件摘要。

`chat_messages.content` 仍只保存用户正文；base64 只存在于当前模型出站请求。上传失败、
解析失败或准备阶段绑定失败不会创建半条消息对；执行阶段失败不会删除已绑定附件。

## 失败与安全边界

- 扩展名、声明 MIME、文件内容、文件名、大小、图片像素、页数和 Office 容器交叉校验。
  ZIP 仅用于 Skill 安装，拒绝危险路径、加密或符号链接条目以及嵌套容器。
- 跨用户、跨 Project、跨 Conversation 的附件按不存在处理；下载和绑定重新校验服务端
  归属，不能由浏览器提交路径。
- Agent 对 `/.attachments/**` 不可写，对 `/.attachments/**/original/**` 不可读，对
  `/.artifacts/**` 不可写；文档只暴露派生 Markdown 虚拟路径。
- staged 附件由后台任务按 TTL 标记为 expired，物理删除成功后释放配额。已绑定附件随所属工作区管理。
- 附件下载/预览响应带 `X-Content-Type-Options: nosniff` 和 `Content-Security-Policy:
  default-src 'none'; sandbox`，不允许浏览器按内容嗅探类型。
- 历史消息引用到已删除/过期附件时，hydration 只输出“附件不可用”提示块，不让单条坏
  附件让整轮执行失败；真正的 `attachment_unavailable` 只在发送前绑定阶段抛出。
- 全局 require_session 验证 Cookie 会话，api/identity.py 读取已验证身份；请求中的 user_id 或 X-Melonclaw-User 只作身份断言，不能覆盖会话身份。服务层仍重新校验唯一有效租户归属与资源所有权。

## 运行与验证

启动与配置见 [README](../../README.md)。上传后可发送附带正文或仅有附件的消息，已发送的附件从历史中下载。
需要调整限制时，修改 `MELONCLAW_ATTACHMENT_*` 参数并重启 Web。 [附件识别验收](chat-attachment-processing.md#运行与验证)
