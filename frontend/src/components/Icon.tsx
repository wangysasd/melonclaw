import type { CSSProperties } from "react";

/**
 * 复用 public/assets/icons/ 下现有 SVG 的图标组件。
 * 使用 CSS mask 方案：currentColor 染色、随字号缩放，不引入图标库。
 * 资源中没有 chevron-left.svg，折叠箭头用 rotate 翻转。
 */
export const ICON_NAMES = [
  "arrow-up",
  "calendar-days",
  "chevron-down",
  "chevron-right",
  "circle-alert",
  "circle-check",
  "copy",
  "external-link",
  "file-text",
  "folder",
  "list-checks",
  "loader-circle",
  "menu",
  "message-circle",
  "plus",
  "search",
  "shield-check",
  "x",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

export interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
  /** 旋转角度（deg），如 chevron-left 用 chevron-right + 180。 */
  rotate?: number;
}

export function Icon({ name, size = 16, className, style, rotate = 0 }: IconProps) {
  const mask = `url(/assets/icons/${name}.svg) center / contain no-repeat`;
  return (
    <span
      aria-hidden="true"
      className={className}
      style={{
        display: "inline-block",
        width: size,
        height: size,
        flexShrink: 0,
        backgroundColor: "currentColor",
        WebkitMask: mask,
        mask,
        transform: rotate ? `rotate(${rotate}deg)` : undefined,
        ...style,
      }}
    />
  );
}
