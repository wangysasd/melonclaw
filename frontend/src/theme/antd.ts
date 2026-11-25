import type { ThemeConfig } from "antd";
import { tokens as t, tokenNumber as n } from "./tokens";

/** Shared visual primitives; component differences are explicit and semantic. */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: t["brand-primary"], colorPrimaryHover: t["brand-primary-hover"], colorPrimaryActive: t["brand-primary-active"], colorInfo: t["brand-primary"], colorLink: t["brand-primary"],
    colorSuccess: t["status-success"], colorWarning: t["status-warning"], colorError: t["status-error"],
    colorBgLayout: t.canvas, colorBgBase: t.panel, colorBgElevated: t.panel, colorText: t.ink,
    colorTextSecondary: t.muted, colorTextTertiary: t.quiet,
    colorTextPlaceholder: t.quiet, colorTextDisabled: t["disabled-text"],
    colorInfoBg: t["brand-soft"], colorInfoBorder: t["brand-soft"],
    colorSuccessBg: t["status-success-bg"], colorSuccessBorder: t["status-success-bg"],
    colorWarningBg: t["status-warning-bg"], colorWarningBorder: t["status-warning-bg"],
    colorErrorBg: t["status-error-bg"], colorErrorBorder: t["status-error-bg"],
    colorBgContainerDisabled: t["disabled-bg"], colorBorder: t.line, colorBorderSecondary: t["line-light"],
    borderRadius: n("radius-control"), borderRadiusSM: n("radius-sm"), borderRadiusLG: n("radius-card"),
    controlHeight: n("control-height-md"), controlHeightSM: n("control-height-sm"), controlHeightLG: n("control-height-lg"),
    fontSize: n("text-md"), fontSizeSM: n("text-xs"), fontSizeLG: n("text-lg"),
    fontFamily: t["font-ui"], fontWeightStrong: n("weight-semibold"), lineHeight: 22 / 14,
    boxShadow: t["shadow-pop"], boxShadowSecondary: t["shadow-modal"],
  },
  components: {
    Layout: { siderBg: t.canvas, headerBg: "transparent", bodyBg: t.canvas },
    Button: {
      fontWeight: n("weight-regular"), paddingInline: n("space-3"), iconGap: n("icon-gap"),
      defaultShadow: "none", primaryShadow: "none", dangerShadow: "none",
      primaryColor: t.panel, defaultColor: t.ink, defaultBg: t.panel,
      defaultBorderColor: t["border-secondary"], defaultHoverBorderColor: t.tertiary,
      defaultHoverColor: t.ink, defaultHoverBg: t.panel, defaultActiveBg: t.panel, defaultActiveColor: t.ink,
    },
    Input: { paddingInline: n("space-3"), activeShadow: "none", colorBorder: t["border-control"], hoverBorderColor: t["border-control"], activeBorderColor: t["brand-primary"] },
    Select: {
      optionSelectedBg: t["brand-soft"], optionSelectedColor: t["brand-primary-hover"], optionSelectedFontWeight: n("weight-regular"),
      optionActiveBg: t.hover, colorBorder: t["border-control"], hoverBorderColor: t["border-control"], activeBorderColor: t["brand-primary"], activeOutlineColor: "transparent",
    },
    Card: { borderRadiusLG: n("radius-card"), bodyPadding: n("space-4"), headerPadding: n("space-4"), headerFontSize: n("text-lg") },
    Modal: { borderRadiusLG: n("radius-overlay"), titleFontSize: n("text-lg"), titleLineHeight: 1.5, paddingContentHorizontalLG: n("space-6") },
    Drawer: { paddingLG: n("space-6") },
    Dropdown: { borderRadiusLG: n("radius-card"), paddingBlock: n("space-2") },
    Popover: { borderRadiusLG: n("radius-card"), titleMinWidth: 0 },
    Tooltip: { borderRadius: n("radius-control") },
    Table: { headerBg: t["table-header-bg"], headerColor: t.muted, rowHoverBg: t["table-row-hover"], borderColor: t["table-divider"], headerBorderRadius: n("radius-card"), cellPaddingBlock: n("space-3"), cellPaddingInline: n("space-4"), cellFontSize: n("text-md") },
    Tabs: { titleFontSize: n("text-md"), horizontalItemPadding: "12px 0", horizontalItemGutter: n("space-6"), horizontalMargin: "0 0 16px 0", itemSelectedColor: t["brand-primary-hover"], itemHoverColor: t["brand-primary-hover"], inkBarColor: t["brand-primary"] },
    Tag: { borderRadiusSM: n("radius-sm"), fontSizeSM: n("text-xs"), lineHeightSM: 22 / 12, defaultBg: t["brand-soft"], defaultColor: t.muted },
    Menu: { itemHeight: n("control-height-lg"), itemBorderRadius: n("radius-control"), iconSize: n("icon-lg"), itemSelectedBg: t["brand-soft"], itemSelectedColor: t["brand-primary-hover"], itemHoverBg: t.hover, itemActiveBg: t.hover },
    Segmented: { itemSelectedBg: t["brand-soft"], itemSelectedColor: t["brand-primary-hover"], itemColor: t.ink, itemHoverColor: t.ink, trackBg: "transparent" },
    Pagination: { itemSize: n("control-height-md"), itemSizeSM: n("control-height-sm"), itemActiveBg: t["brand-soft"] },
    Form: { itemMarginBottom: n("space-4"), labelFontSize: n("text-md"), labelColor: t.ink, verticalLabelPadding: "0 0 8px" },
  },
};

/** Semantic slot styles keep modal/drawer spacing consistent without page overrides. */
export const overlayStyles = {
  header: { padding: `${t["space-5"]} ${t["space-6"]}`, marginBottom: 0 },
  body: { padding: `${t["space-5"]} ${t["space-6"]}` },
  footer: { padding: `${t["space-4"]} ${t["space-6"]}`, marginTop: 0 },
};
