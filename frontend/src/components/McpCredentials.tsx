import { Button, Input } from "antd";

export interface CredentialRow { key: string; value: string; saved: boolean; removed: boolean }
export function McpCredentials({ label, rows, onChange }: {
  label: string; rows: CredentialRow[]; onChange: (rows: CredentialRow[]) => void;
}) {
  return <fieldset className="mcp-credentials"><legend>{label}</legend>
    {rows.map((row, index) => <div className="mcp-credential-row" key={index}>
      <Input aria-label={`${label}名称 ${index + 1}`} placeholder="名称" value={row.key} disabled={row.saved || row.removed}
        onChange={event => onChange(rows.map((item, i) => i === index ? { ...item, key: event.target.value } : item))} />
      <Input.Password aria-label={`${label}值 ${index + 1}`} autoComplete="new-password" value={row.value} disabled={row.removed}
        placeholder={row.removed ? "将移除" : row.saved ? "已配置；留空保留，输入替换" : "值"}
        onChange={event => onChange(rows.map((item, i) => i === index ? { ...item, value: event.target.value } : item))} />
      <Button onClick={() => onChange(row.saved ? rows.map((item, i) => i === index ? { ...item, removed: !item.removed, value: "" } : item) : rows.filter((_, i) => i !== index))}>
        {row.removed ? "保留" : "移除"}
      </Button>
    </div>)}
    <Button type="link" onClick={() => onChange([...rows, { key: "", value: "", saved: false, removed: false }])}>＋ 添加{label}</Button>
  </fieldset>;
}
