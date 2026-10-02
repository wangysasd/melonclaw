import { useMemo } from "react";
import { textDiff } from "../lib/textDiff";

export function TextDiff({ before, after, fileName = "修改差异" }: { before: string; after: string; fileName?: string }) {
  const { lines, coarse } = useMemo(() => textDiff(before, after), [before, after]);
  const body = <>
    <p className="result-muted">新增以 + 标记，删除以 − 标记。{coarse ? "内容较长，显示整段替换预览。" : ""}</p>
    {before === after ? <p>内容没有变化。</p> : <pre className="result-diff" aria-label={`${fileName}差异`}><code>{lines.map((line, index) => <span className={`diff-${line.kind}`} key={index}>{line.kind === "added" ? "+" : line.kind === "removed" ? "−" : " "} {line.text}{"\n"}</span>)}</code></pre>}
  </>;
  return lines.length > 20 ? <details className="result-details"><summary>查看 {fileName}（{lines.length} 行）</summary>{body}</details> : <div className="result-diff-wrapper">{body}</div>;
}
