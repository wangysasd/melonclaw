# 站点访问提示

状态：已实现。核对日期：2026-10-17。

## 当前展示与数据流

components/ComplianceNotice.tsx 的 AccessNoticeGate 位于 App 的 AuthGate 外层。仅非 rms 品牌启用；品牌去除首尾空格并忽略大小写。未确认时仅挂载 Ant Design Modal，不挂载认证或工作区子树；确认后写入 melonclaw.access_notice.v1，再进入登录或工作区。提示使用 14px 正文，风险文字加粗。

弹窗无关闭按钮，禁止遮罩和 Escape 关闭。确认按同一浏览器、同源存储记忆，不按账号记忆；存储被禁用时，本次确认仍有效，下次刷新重新提示。RMS 品牌直接进入认证流程。

## 边界

该提示不替代 Cookie 认证、服务端授权或法律效力判断，不提供服务端签署证据。当前 ChatView 不挂载页脚声明，不能把测试中存在的 ComplianceStatement 组件描述为用户已能看到的页面功能。该闲置组件的去留仍属于清理审计 CLN-037，本次只修正文档，不恢复或删除 UI。

## 验证

frontend/tests/ComplianceNotice.test.tsx 覆盖提示门控、确认记忆与存储失败。页脚组件的孤立渲染测试不能证明 ChatView 挂载。真实浏览器可在隔离测试配置下清除确认键验证首次提示；本轮仅进行文档检查，不操作用户浏览器存储。
