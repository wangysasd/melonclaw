import type { ThemeConfig } from "antd";

/**
 * antd 主题：把 Apple 设计语言 token（styles/global.css）
 * 注入 antd，保持「中性灰 + 系统蓝」视觉统一。
 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: "#0071E3",
    colorInfo: "#0071E3",
    colorLink: "#0071E3",
    colorSuccess: "#34C759",
    colorWarning: "#FF9500",
    colorError: "#FF3B30",
    colorBgLayout: "#F5F5F7",
    colorBgBase: "#FFFFFF",
    colorText: "#1D1D1F",
    colorTextSecondary: "#6E6E73",
    colorBorderSecondary: "#D2D2D7",
    borderRadius: 10,
    fontSize: 16,
    fontFamily: "var(--font-ui)",
  },
  components: {
    Layout: {
      siderBg: "#F5F5F7",
      headerBg: "transparent",
      bodyBg: "#F5F5F7",
    },
    Modal: {
      borderRadiusLG: 16,
    },
  },
};
