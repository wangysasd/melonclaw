# RMS 分栏登录页视觉验证

日期：2026-10-06

## 视觉基准与证据

- source visual truth path: `note/rms-login-split-reference.png`（第二幅图的分栏风格）和 `note/rms-login-form-reference.png`（第一幅图的表单）。
- 用户调整：删除「RMS · 投研助手」「欢迎回来」「欢迎使用 RMS 投研助手」，右侧保留输入和登录动作，免密采用第一幅图文字按钮。
- implementation screenshot path: `note/rms-login-split-desktop.png`。
- viewport: 1440 × 1024 CSS px，deviceScaleFactor=1；实现截图同尺寸。两张概念稿约 1484 × 1060，比较时归一化为 1440 × 1024，比例偏差小于 0.5%。
- state: 未登录，空表单，免密入口由后端开放。
- full-view comparison evidence: `note/rms-login-split-comparison.jpg`，左为第二幅原稿、右为用户调整后的实现。
- focused region comparison evidence: `note/rms-login-split-form-comparison.jpg`，左为第一幅图的表单、右为实现的右侧表单。
- mobile: `note/rms-login-split-mobile.png`，390 × 844，Logo 和单列表单，无横向溢出。图片存于忽略版本管理的本地 note，仅用作验证证据。

## 五项视觉检查

- 字体：系统中文字体，正文和标签保持 14px；品牌大标题沿用第二幅图的层级，右侧欢迎语按用户要求删除。生成稿的文字尺寸更大，遵循项目字号约定是明确取舍。
- 间距和布局：左侧 56%、右侧 44%，全高分栏，右侧表单宽 360px 垂直居中；窄屏 342px，隐藏装饰和大标题。无套卡片、页脚或额外图注。
- 色彩：浅蓝图片与白色表单区，共享主题、正文/边框/圆角 Token；品牌蓝由共享 ConfigProvider 配置。
- 资产：独立玻璃环 WebP（1132 × 1390，66546 bytes），无文字或 UI，原 RMS 字标复用。生成图保留了玻璃环的下半构图和柔和地面反射；细节为生成资产的预期差异。旧建筑配图已移除。
- 内容：浏览器页面文本仅有「让研究更进一步」、用户 ID、密码、登录及免密入口，指定删除的三处文案均不出现。

## 对比迭代

1. [P2] 原居中 grid 的 justify-items 使右侧表单缩窄到 198px；已在 RMS 页面覆盖为 place-items: stretch。修正后桌面宽 360px、窄屏宽 342px，截图和 DOM 测量一致。
2. 配图加载后完成全图与表单近景比较；分栏比例、玻璃环构图和精简动作一致，无待处理 P0/P1/P2。

## 验证清单

- [x] scripts/check.sh 全部通过。
- [x] 桌面与窄屏截图、无横向溢出、删文案检查。
- [x] 装饰图片正常加载（naturalWidth=1132）。
- [x] 原账户测试覆盖密码登录、错误保留、密码不 trim 和后端免密开关；认证逻辑未改变。



## 右侧居中品牌构图补充验收

本轮按第一幅图补齐右侧 Logo 和留白，保持指定欢迎语删除。最新实现截图为 `note/rms-login-centered-desktop.png`（1440×1024、密度1）及 `note/rms-login-centered-mobile.png`（390×844、密度1）。`note/rms-login-centered-comparison.jpg` 将第一幅图居中区域和实现右侧并排对照：160px 居中字标、360px 表单、免密文字按钮和足够留白均符合意图。正文14px、字段间距32px、触控44px以及白底是项目视觉约定；两句欢迎语的缺失是用户要求。资产复用原字标，无新增图片；颜色沿用共享主题。桌面左侧保留原玻璃环图，窄屏仅显示右侧一个Logo与342px表单，无横向溢出。无新P0/P1/P2；前端lint、类型检查和测试通过。



## 表单蓝色渐变光晕补充

用户明确要求恢复第一张概念图的蓝色渐变光晕。在右侧登录区使用品牌蓝径向渐变（中心10%、中间6%/2%、边缘透明）；不新增拦截交互的覆盖层。截图 `note/rms-login-glow-desktop.png`，1440×1024、密度1；对照 `note/rms-login-glow-comparison.jpg`。字体、间距、图片及文案沿用前次已验收状态，输入保持白底，装饰色柔和淡出，Logo和表单清晰。390px宽度也有渐变且无横向溢出。没有新增P0/P1/P2问题。



## 浏览器标注调整

按用户标注移除左上角 Logo，并优化左侧标题为系统字体常规字重、28–44px、深蓝灰（品牌蓝与次要文字色混合），增加适度字距。截图 `note/rms-login-title-refined.png`，1578×1314、密度1。用户标注图已直接检查，最新截图确认左侧Logo不存在、标题比原黑色粗字更轻、更协调，右侧Logo、光晕及登录控件保留。浏览器测量左侧Logo数为0、没有横向溢出。无新增P0/P1/P2。



## 光晕范围扩大

按用户标注将蓝色光晕椭圆半径扩大为右侧容器宽度的85%、高度的68%，保留原色彩强度和淡出节点。截图 `note/rms-login-large-glow.png`（1578×1314、密度1）已检查，蓝色覆盖右侧大部分空间，边缘柔和，输入框仍白底清晰。390px窄屏渐变有效且无横向溢出，无布局/交互变化或新增P0/P1/P2。

final result: passed


## MelonClaw 第二套浅蓝方案（2026-10-06）

Source visual truth: `note/melon-login-target.png`（本轮显示顺序第二图）。
Implementation: `note/melon-login-desktop.png`、`note/melon-login-mobile.png`。
Viewport: desktop 1440×1024 CSS pixels / 1440×1024 screenshot, density 1；mobile 390×844, density 1。Source 1488×1056，完整画面统一缩放到720×512，仅用于对照。
Full-view evidence: `note/melon-login-comparison.png`。已实际打开合并图片比较，窄屏截图单独检查；没有需要额外局部放大的剩余问题。
State: 未登录，brand 空，后端允许免密。手机截图为必填失败状态。

Comparison history:
- 首轮：Logo 和表单偏窄（P2），扩大到360px字标、最大430px表单；Logo 底色矩形（P2），亮度微调后与背景融合。
- 再次截图与合并对照：以上问题消除，无剩余 P0/P1/P2。表单采用项目14px正文和44px控件，较概念图更克制，属于明确的项目字体约束；左侧纯色背景更清晰。

Fidelity surfaces:
- Typography：系统字体、标题600字重、14px正文，未换行截断；字标保留真实素材。
- Layout：44/56左右构图、开放式表单、留白和玻璃核心；900px以下装饰隐藏，无横向溢出，控件44px。
- Colors：共享 #0071e3 品牌蓝、浅蓝白背景、灰色辅助文案。浏览器计算样式确认14px与品牌蓝。
- Images：本地WebP玻璃轨道AI核心，约80KB；原PNG字标局部裁切，不重绘。装饰空alt不干扰辅助阅读。
- Copy：开启智能协作／从一个想法，到无限可能，用户ID、密码、登录和受后端控制的免密入口与目标一致。

Interactions：浏览器空表单提交显示两项必填提示；现有自动化验证登录失败保留输入、密码不trim、免密受配置控制。浏览器console只有Vite调试信息，无错误。未使用真实账户登录。
Checks：scripts/check.sh 全部通过，最终CSS修订后重跑前端检查。
final result: passed
