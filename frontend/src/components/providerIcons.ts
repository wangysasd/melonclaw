/**
 * 供应商 logo 映射：从 Yuxi `web/src/utils/modelIcon.js` 抽取。
 *
 * Yuxi 运行时外链 `registry.npmmirror.com/@lobehub/icons-static-svg` 的 SVG；
 * 这里把用到的 14 个文件 vendor 到 `public/assets/provider-logos/`，
 * 背景色/缩放/滤镜沿用 Yuxi 的 `avatar()` 参数，避免运行时依赖外网。
 * 单色图标（currentColor 画的）用白色滤镜压在深色底上；`*-color` 彩色图标不过滤镜。
 */

const WHITE_ICON_FILTER = "brightness(0) invert(1)";

export interface ProviderAvatar {
  icon: string;
  background: string;
  scale: number;
  filter: string;
}

const avatar = (
  icon: string,
  background: string,
  scale = 0.75,
  filter = WHITE_ICON_FILTER,
): ProviderAvatar => ({
  icon: `/assets/provider-logos/${icon}.svg`,
  background,
  scale,
  filter,
});

export const PROVIDER_AVATARS: Record<string, ProviderAvatar> = {
  openai: avatar("openai", "#000"),
  deepseek: avatar("deepseek", "#4d6bfe"),
  "alibaba-cn": avatar("bailian-color", "#fff", 0.75, "none"),
  alibaba: avatar("alibaba", "#ff6003", 0.8),
  "alibaba-coding-plan-cn": avatar("alibabacloud", "#ff6a00", 0.7),
  "alibaba-coding-plan": avatar("alibabacloud", "#ff6a00", 0.7),
  zhipuai: avatar("zhipu", "#3859ff"),
  "zhipuai-coding-plan": avatar("zhipu", "#3859ff"),
  zai: avatar("zai", "#000", 0.6),
  "zai-coding-plan": avatar("zai", "#000", 0.6),
  "xiaomi-token-plan-cn": avatar("xiaomimimo", "#000", 0.7),
  xiaomi: avatar("xiaomimimo", "#000", 0.7),
  "kimi-for-coding": avatar("moonshot", "#16191e"),
  "moonshotai-cn": avatar("moonshot", "#16191e"),
  moonshotai: avatar("moonshot", "#16191e"),
  "minimax-cn": avatar(
    "minimax",
    "linear-gradient(to right, #e2167e, #fe603c)",
  ),
  minimax: avatar("minimax", "linear-gradient(to right, #e2167e, #fe603c)"),
  openrouter: avatar(
    "openrouter",
    "#000",
    0.75,
    "brightness(0) saturate(100%) invert(94%) sepia(94%) saturate(1636%) hue-rotate(24deg) brightness(105%) contrast(106%)",
  ),
  modelscope: avatar("modelscope", "#624aff"),
  opencode: avatar("opencode", "#000"),
  "opencode-go": avatar("opencode", "#000"),
  "siliconflow-cn": avatar("siliconcloud", "#6e29f6", 0.7),
  siliconflow: avatar("siliconcloud", "#6e29f6", 0.7),
};

/** 查不到映射（管理员自建的非标供应商）返回 null，调用方回落到首字母头像。 */
export function getProviderAvatar(providerKey: string): ProviderAvatar | null {
  return PROVIDER_AVATARS[providerKey] ?? null;
}
