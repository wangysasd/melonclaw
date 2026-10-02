import type { ThemeConfig } from "antd";
import { tokens as t, tokenNumber as n } from "./tokens";

/** Shared visual primitives; component differences are explicit and semantic. */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: t["brand-primary"], colorInfo: t["brand-primary"], colorLink: t["brand-primary"],
    colorSuccess: t["status-success"], colorWarning: t["status-warning"], colorError: t["status-error"],
    colorBgLayout: t.canvas, colorBgBase: t.panel, colorText: t.ink,
    colorTextSecondary: t.muted, colorTextTertiary: t.tertiary,
    colorTextPlaceholder: t.tertiary, colorTextDisabled: t["disabled-text"],
    colorBgContainerDisabled: t["disabled-bg"], colorBorder: t.line, colorBorderSecondary: t["line-light"],
    borderRadius: n("radius-control"), borderRadiusSM: n("radius-sm"), borderRadiusLG: n("radius-card"),
    controlHeight: n("control-height-md"), controlHeightSM: n("control-height-sm"), controlHeightLG: n("control-height-lg"),
    fontSize: n("text-md"), fontSizeSM: n("text-xs"), fontSizeLG: n("text-lg"),
    fontFamily: t["font-ui"], fontWeightStrong: n("weight-semibold"), lineHeight: 22 / 14,
    boxShadow: t["shadow-pop"], boxShadowSecondary: t["shadow-modal"],
  },
  components: {
    Layout: { siderBg: t.canvas, headerBg: "transparent", bodyBg: t.canvas },
    Button: { fontWeight: n("weight-medium"), paddingInline: n("space-3"), iconGap: n("icon-gap"), defaultShadow: "none", primaryShadow: "none", dangerShadow: "none" },
    Input: { paddingInline: n("space-3") },
    Select: { optionSelectedBg: t["brand-soft"], optionSelectedColor: t["brand-primary"], optionSelectedFontWeight: n("weight-medium") },
    Card: { borderRadiusLG: n("radius-card"), bodyPadding: n("space-4"), headerPadding: n("space-4"), headerFontSize: n("text-lg") },
    Modal: { borderRadiusLG: n("radius-overlay"), titleFontSize: n("text-lg"), titleLineHeight: 1.5, paddingContentHorizontalLG: n("space-6") },
    Drawer: { paddingLG: n("space-6") },
    Dropdown: { borderRadiusLG: n("radius-card"), paddingBlock: n("space-2") },
    Popover: { borderRadiusLG: n("radius-card"), titleMinWidth: 0 },
    Tooltip: { borderRadius: n("radius-control") },
    Table: { headerBg: t.canvas, headerColor: t.muted, rowHoverBg: t.hover, cellPaddingBlock: n("space-3"), cellPaddingInline: n("space-4"), cellFontSize: n("text-md") },
    Tabs: { titleFontSize: n("text-md"), horizontalItemPadding: "12px 0", horizontalItemGutter: n("space-6"), horizontalMargin: "0 0 16px 0" },
    Tag: { borderRadiusSM: n("radius-sm"), fontSizeSM: n("text-xs"), lineHeightSM: 22 / 12 },
    Menu: { itemHeight: n("control-height-lg"), itemBorderRadius: n("radius-control"), iconSize: n("icon-lg"), itemSelectedBg: t["brand-soft"], itemSelectedColor: t["brand-primary"] },
    Form: { itemMarginBottom: n("space-4"), labelFontSize: n("text-md"), verticalLabelPadding: "0 0 8px" },
    Pagination: { itemSize: n("control-height-md"), itemSizeSM: n("control-height-sm") },
  },
};
