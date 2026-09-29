import type { SkillContentPreview } from "../types/api";

/** 只读文本预览：不渲染包内 HTML、不执行脚本，也不授予依赖所声明的权限。 */
export function SkillPreview({ preview, showDiff = false }: { preview: SkillContentPreview; showDiff?: boolean }) {
  const status = { available: "已具备", missing: "缺少", manual: "需自行确认" };
  return <div className="skill-preview">
    <details open><summary>SKILL.md 正文{preview.body_truncated ? "（仅显示前 64,000 字符）" : ""}</summary>
      <pre>{preview.body}</pre>
    </details>
    <details><summary>文件清单（{preview.files.length}）</summary>
      <ul>{preview.files.map((file) => <li key={file.path}>{file.path} · {file.size} 字节</li>)}</ul>
    </details>
    {showDiff ? <details open><summary>更新差异</summary>
      <p>新增 {preview.changes.added.length} · 修改 {preview.changes.modified.length} · 删除 {preview.changes.removed.length}</p>
      {(["added", "modified", "removed"] as const).map((kind) => <ul key={kind}>
        {preview.changes[kind].map((path) => <li key={path}>{({ added: "新增", modified: "修改", removed: "删除" })[kind]}：{path}</li>)}
      </ul>)}
      <pre>{preview.diff || "SKILL.md 无文本变化"}</pre>
      <p>正文差异最多比较前 64,000 字符；文件摘要覆盖全部内容。</p>
    </details> : null}
    <details open><summary>依赖检查</summary>
      {preview.dependency_checks.length ? <ul>{preview.dependency_checks.map((item) =>
        <li key={`${item.kind}:${item.name}`}>{item.kind} · {item.name}：{status[item.status]}</li>)}
      </ul> : <p>此 Skill 未声明依赖。</p>}
      <p>检查仅提示所需条件，不安装程序、不执行脚本、不授予权限。MCP 已配置不代表连接已验证。</p>
    </details>
  </div>;
}
