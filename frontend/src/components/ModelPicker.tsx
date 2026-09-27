import { Button, Select } from "antd";

import type { ModelOption } from "../types/api";
import { getProviderAvatar } from "./providerIcons";
import { Icon } from "./Icon";

interface ModelPickerProps {
  options: ModelOption[];
  value: string;
  disabled?: boolean;
  onChange: (modelId: string) => void;
  /** 点击「添加自定义模型」：跳到插件页的模型供应商 TAB。 */
  onAddCustomModel?: () => void;
}

/** 下拉行：供应商 logo 与模型显示名。 */
function ModelRow({ option }: { option: ModelOption }) {
  const avatar = getProviderAvatar(option.provider_key);
  return (
    <span className="model-option">
      {avatar ? (
        <span
          className="model-option-logo"
          style={{ background: avatar.background }}
          aria-hidden
        >
          <img
            src={avatar.icon}
            alt=""
            loading="lazy"
            style={{ filter: avatar.filter }}
          />
        </span>
      ) : (
        <span className="model-option-logo is-fallback" aria-hidden>
          {option.display_name.slice(0, 1).toUpperCase()}
        </span>
      )}
      <span className="model-option-name" title={option.display_name}>
        {option.display_name}
      </span>

    </span>
  );
}

function toSelectOption(option: ModelOption) {
  return {
    value: option.id,
    label: <ModelRow option={option} />,
    disabled: !option.available,
  };
}

/**
 * 聊天框模型选择器：按截图，分「内置模型 / 自定义模型」两组展示，
 * 每行带供应商 logo；底部「添加自定义模型」跳到模型供应商配置 TAB。
 */
export function ModelPicker({
  options,
  value,
  disabled,
  onChange,
  onAddCustomModel,
}: ModelPickerProps) {
  const systemOptions = options.filter((item) => item.available && (item.source === "system" || item.scope === "global"));
  const customOptions = options.filter((item) => item.available && item.scope === "user");
  const groups = [
    ...(systemOptions.length > 0
      ? [{ label: "内置模型", options: systemOptions.map(toSelectOption) }]
      : []),
    ...(customOptions.length > 0
      ? [{ label: "自定义模型", options: customOptions.map(toSelectOption) }]
      : []),
  ];
  return (
    <Select
      className="model-picker"
      classNames={{ popup: { root: "model-picker-dropdown" } }}
      aria-label="选择模型"
      popupMatchSelectWidth={260}
      styles={{ popup: { root: { maxWidth: "calc(100vw - 24px)" } } }}
      placeholder="请先配置模型"
      value={value || undefined}
      disabled={disabled}
      title="模型选择从下一条消息生效"
      notFoundContent="暂无可用模型，请先添加模型"
      options={groups}
      onChange={onChange}
      labelRender={(props) => {
        const current = options.find((item) => item.id === props.value);
        return <span>{current?.display_name ?? props.label}</span>;
      }}
      dropdownRender={(menu) => (
        <>
          {menu}
          <div className="model-picker-footer">
            <Button
              type="text"
              icon={<Icon name="plus" size={14} />}
              onClick={onAddCustomModel}
            >
              添加自定义模型
            </Button>
          </div>
        </>
      )}
    />
  );
}
