# 站点备案展示

状态：已实现。核对日期：2026-11-26。

## 当前展示与数据流

`App` 直接挂载 `AuthGate`。未登录显示登录页，已登录进入工作区。首次访问不再展示风险确认弹窗，也不读写 `melonclaw.access_notice.v1`。浏览器里残留的旧确认记录没有效果。

## 边界

页面不再用弹窗拦截未确认访问。当前 ChatView 不挂载页脚声明，不能把测试中存在的 ComplianceStatement 组件描述为用户已能看到的页面功能。该闲置组件的去留仍属于清理审计 CLN-037。

## 验证

frontend/tests/ComplianceNotice.test.tsx 覆盖备案号链接和闲置声明组件的孤立渲染。workspace-navigation 渲染 `App` 时直接进入工作区，不再点击确认按钮。页脚组件的孤立渲染测试不能证明 ChatView 挂载。

## 工信部备案页脚

LoginPage 在登录表单所在面板内挂载 FilingFooter，登录后的工作区不挂载，各品牌均显示“京ICP备2026082777号-1”，新窗口打开 https://beian.miit.gov.cn/，带 noopener noreferrer。页脚定位在登录面板底部，沿用面板留白避免覆盖表单；仅显示灰色文字链接，无独立背景条，默认无下划线，悬停显示下划线。公安联网备案数据码不用于展示；正式公安备案号待审核通过后另行添加。
