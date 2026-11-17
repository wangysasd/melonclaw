# 普通聊天附件上传与处理

已实现 · 2026-11-17

按[附件设计](../../design-docs/chat-attachment-processing.md)完成普通会话与项目会话的 20 种附件类型、同步 OCR、图片能力分流和 XLS 本地读取。

- [x] 统一白名单、文件校验和限额，接入 XLS 和图片首帧转换。
- [x] 复用 DeepSeek OCR 客户端和附件租约，接通上传、补识别和持久化。
- [x] 按实际模型和附件状态绑定消息，在模型出站前选择图片或派生正文。
- [x] 接通前端 PDF 页数预检、上传反馈、切换模型和队列场景。
- [x] 完成回归检查、真实 OCR 样本验收和文档同步。

真实 DeepSeek 样本覆盖 PDF、DOC、DOCX、PPT、PPTX，及 PNG、JPEG、WebP、GIF、BMP、单页 TIFF 和 WebP/GIF 动图首帧。原样提交双页 TIFF 只返回第一页，改为按原顺序转 PDF 后，`rich_layout_recognition` 完整返回两页。合成 XLS 验证了本地读取两个工作表。

独立 PostgreSQL 验证原件下载、单页 TIFF PNG 预览、多页 TIFF 同步 OCR、并发认领、上传幂等及按消息绑定表示。浏览器用同一双页 PDF 验证上限为一页时拦截、上限为两页时通过。

`scripts/check.sh` 全部通过，包含启用 PostgreSQL 的集成测试。前端生产构建通过。图片出站由测试模型验证，真实视觉供应商调用留给配置完成后的部署验收。
