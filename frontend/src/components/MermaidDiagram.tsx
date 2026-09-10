import { useEffect, useState } from "react";
import Mermaid from "@ant-design/x/es/mermaid";
import mermaid from "mermaid";

const config = {
  securityLevel: "strict" as const,
  startOnLoad: false,
  theme: "default" as const,
  flowchart: { htmlLabels: false },
};

export default function MermaidDiagram({ source }: { source: string }) {
  const [validation, setValidation] = useState<"pending" | "valid" | "invalid">("pending");
  useEffect(() => {
    let cancelled = false;
    setValidation("pending");
    // 保持聊天原有远程图片和 HTML 禁止策略，且避免超大图表阻塞渲染。
    if (source.length > 20000 || /<|\bimg\s*:|\bimage\b|https?:|\/\//im.test(source)) {
      setValidation("invalid");
      return;
    }
    mermaid.initialize(config);
    void mermaid.parse(source, { suppressErrors: true }).then((valid) => {
      if (!cancelled) setValidation(valid ? "valid" : "invalid");
    }).catch(() => {
      if (!cancelled) setValidation("invalid");
    });
    return () => { cancelled = true; };
  }, [source]);
  if (validation === "pending") {
    return <div className="mermaid-pending">正在检查图表…</div>;
  }
  if (validation !== "valid") {
    return <div><p>图表无法安全渲染，保留源码：</p><pre><code>{source}</code></pre></div>;
  }
  return <Mermaid config={config} header="流程图" actions={{ enableDownload: false, enableCopy: false }}>{source}</Mermaid>;
}
