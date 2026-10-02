import { useEffect, useRef, useState } from "react";
import { Modal } from "antd";
import { copyText } from "../lib/clipboard";
import { downloadBlob } from "../lib/resultExport";
import Mermaid from "@ant-design/x/es/mermaid";
import mermaid from "mermaid";

const config = {
  securityLevel: "strict" as const,
  startOnLoad: false,
  theme: "default" as const,
  flowchart: { htmlLabels: false },
};

export default function MermaidDiagram({ source }: { source: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [showSource, setShowSource] = useState(false);
  const [enlarged, setEnlarged] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [copyFeedback, setCopyFeedback] = useState("复制代码");
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
  const exportSvg = () => {
    const svg = ref.current?.querySelector("svg");
    if (!svg) { setFeedback("图形尚未就绪，请切回图形后重试。"); return; }
    downloadBlob(new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml;charset=utf-8" }), "流程图.svg");
    setFeedback("");
  };
  return <div className="result-mermaid" ref={ref}>
    {showSource ? <pre><code>{source}</code></pre> : <Mermaid config={config} styles={{ graph: { border: 0 } }} header={null} actions={{ enableZoom: false, enableDownload: false, enableCopy: false }}>{source}</Mermaid>}
    <div className="result-actions result-secondary-actions">
      <button type="button" aria-pressed={!showSource} onClick={() => setShowSource(false)}>图形</button>
      <button type="button" aria-pressed={showSource} onClick={() => setShowSource(true)}>源码</button>
      <button type="button" onClick={() => setEnlarged(true)}>放大</button>
      <button type="button" onClick={exportSvg}>导出 SVG</button>
      <button type="button" onClick={() => void copyText(source).then(() => setCopyFeedback("已复制"), () => setCopyFeedback("复制失败"))}>{copyFeedback}</button>
    </div>
    {feedback ? <p role="status">{feedback}</p> : null}
    <Modal open={enlarged} onCancel={() => setEnlarged(false)} title="流程图" footer={null} width="90vw" destroyOnHidden>
      <Mermaid config={config} styles={{ graph: { border: 0 } }} header={null} actions={{ enableZoom: false, enableDownload: false, enableCopy: false }}>{source}</Mermaid>
    </Modal>
  </div>;
}
