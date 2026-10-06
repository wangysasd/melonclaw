# 账户 review 修复（2026-10-06）

## 背景与目标

账户未提交改动有来源白名单不一致、列表响应乱序、畸形密码散列异常和启动就绪误报。修复四项问题，不修改 schema，不清库，不改变 SSE 生命周期。

## 方案与关键选择

`core/config.py` 解析精确 Origin 白名单，拒绝通配符；`api/app.py` 保存应用启动快照，CORS 与 `api/auth.py` 共用。同源无需白名单，同站跨域需要白名单，浏览器标记 cross-site 的写请求始终拒绝。Cookie 保留 Strict，不支持真正跨站 Cookie 直连，部署使用同源代理。有关同站 Cookie 与 Fetch Metadata 的浏览器语义参见 [MDN Cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie) 和 [Sec-Fetch-Site](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site)。无 Origin 的非浏览器请求保持原行为，来源检查不是客户端认证。

`AccountManagement` 每次刷新递增序号，只有最新请求能更新用户、租户、错误提示或 loading；卸载使请求失效。写请求成功后再刷新，旧响应不能覆盖新数据。请求不主动取消，不影响服务器已接受的管理操作。

`verify_password` 只接受当前 hash_password 生成的 scrypt 格式：32 位小写十六进制盐、128 位小写十六进制摘要。畸形值返回 False，登录路由返回统一用户 ID 或密码错误，无旧格式兼容。

`GET /api/ready` 无需会话，仅返回 ready 布尔值；读取既有 manager.ready，完成初始化返回 200，初始化中或失败返回 503，设置 no-store。脚本只接受成功状态；公开响应不包含内部错误、配置或凭据，详细状态仍经认证。探测不执行额外数据库查询，不代表模型配置或 MCP 连通。

## 数据与事件流

来源配置 → 应用快照 → CORS 预检与写请求检查 → Cookie 登录；刷新发起 → 序号比较 → 最新结果入状态；损坏散列 → False → 统一 401；后台初始化 → manager.ready → HTTP 200/503 → 启动脚本等待。

## 运行与验证

启动仍用 `scripts/start.sh --profile dev`。无需数据库初始化或升级。运行 `scripts/check.sh`，后端测试覆盖 CORS 预检、来源矩阵、Cookie 会话、损坏散列和公开探测；前端测试覆盖迟到成功与迟到错误响应。真实 PostgreSQL 测试依赖独立测试连接，未配置则跳过，本次不操作业务数据库。

针对性验证记录：后端 15 passed（来源、Cookie、散列和 readiness），前端 8 passed（含四种乱序组合）。原 it.fails 的创建用户流程依赖未完成的租户列表，会提前失败；现改用创建租户，确保保存与第二次刷新实际发生，并断言旧响应不能提前结束新请求 loading。测试环境额外定义导航开关默认 false，避免工作区导航改动的编译常量缺失。

最终验证：`scripts/check.sh` 全部通过（后端编译、测试、ruff、锁文件、前端 lint、类型检查、测试及文档链接）；`bash -n scripts/start.sh` 通过。未执行 db-init、db-update 或清库，未手工验证真实浏览器跨域部署。
