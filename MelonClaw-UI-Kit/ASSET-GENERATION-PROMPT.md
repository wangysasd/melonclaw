# 品牌素材生成记录

方法：内置图像生成工具，编辑仓库现有的 melon-claw.png；未使用 API/CLI 后备路径。
输出：assets/melonclaw-mark-white.png，白底 RGB 源图。应保留原始 Logo，此图是适用于小尺寸 UI 的品牌提案。

最终生成 Prompt：

> Edit the attached MelonClaw brand logo into one compact text-free square app icon asset. Remove ALL text including the word melonclaw. Use a completely opaque uniform PURE WHITE #FFFFFF background. This background is essential: NO transparency, NO checkerboard, NO texture, NO gray grid, no colored tile, no shadow. Keep precisely the existing 3 dark warm-brown watermelon-seed paw toes and the single rounded watermelon wedge as the large paw pad. Three seeds above the pad, never any seeds inside the red fruit flesh. Preserve the rounded friendly shapes, red flesh, pale-green pith, dark-green rind, dark-brown seeds. Make shapes near-flat and crisp. The standalone mark should be centered and occupy 90% of the full square canvas height and about 90% width, about 5% clear white padding. Designed to still read as a watermelon paw at 32–40px. Color palette: coral-red flesh #F56B70, pale green pith, deep green rind #176B4A, dark brown seeds #403329. Absolutely no text, no letters, no watermark, no face, no extra decoration, no mosaic or transparency visualization. Deliver one finished PNG image of this single mark on a pure white background.

验证：已查看生成图片，确认无文字、无重复瓜子、主体为原有瓜爪意象。文件可正常解码，1254×1254，RGB；背景为近白色，未宣称透明。小尺寸渲染及实际界面匹配需在 Codex 接入阶段验收。
