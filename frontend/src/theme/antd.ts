import type { ThemeConfig } from "antd";

/**
 * antd 主题：把现有设计 token（styles/global.css）
 * 注入 antd，保持「清爽浅色 + 西瓜绿品牌」视觉延续。
 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: "#176B4A",
    colorInfo: "#176B4A",
    colorLink: "#176B4A",
    colorSuccess: "#176B4A",
    colorWarning: "#855C18",
    colorError: "#B93848",
    colorBgLayout: "#F8FAF6",
    colorBgBase: "#FFFFFF",
    colorText: "#20392E",
    colorTextSecondary: "#607266",
    colorBorderSecondary: "#DCE5DC",
    borderRadius: 12,
    fontSize: 16,
    fontFamily: "var(--font-ui)",
  },
  components: {
    Layout: {
      siderBg: "#F2F6F0",
      headerBg: "transparent",
      bodyBg: "#F8FAF6",
    },
    Modal: {
      borderRadiusLG: 16,
    },
  },
};
