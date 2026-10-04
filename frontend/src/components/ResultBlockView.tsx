import { lazy, Suspense, useMemo } from "react";
import { assetKey, parseResultBlockDetailed } from "../lib/resultBlocks";
import { ResultRenderBoundary } from "./ResultRenderBoundary";
import { ResultAsset } from "./ResultAsset";
import { ResultSources } from "./ResultSources";
import { ResultTable } from "./ResultTable";
import { TextDiff } from "./TextDiff";
import { useResultScope } from "./ResultContext";
const ResultChartCanvas = lazy(() => import("./ResultChartCanvas"));

export default function ResultBlockView({ raw }: { raw: string }) {
  const scope = useResultScope();
  const { result, error } = useMemo(() => parseResultBlockDetailed(raw), [raw]);
  if (!result) return <div className="result-invalid"><p role="status">{error} 保留原始内容：</p><pre><code>{raw}</code></pre></div>;
  if (result.type === "file" && scope?.fileCardsAtEnd && scope.deliveredFiles?.some((ref) => assetKey(ref) === assetKey(result.ref))) return null;
  switch (result.type) {
    case "file": case "image": return <ResultAsset asset={result.ref} image={result.type === "image"} caption={result.caption} />;
    case "diff": return <section className="result-card"><p className="result-title">{result.file_name}</p><TextDiff before={result.before} after={result.after} fileName={result.file_name} /></section>;
    case "table": return <section className="result-card"><p className="result-title">{result.title}</p><ResultTable title={result.title} columns={result.columns} rows={result.rows} /></section>;
    case "sources": return <ResultSources items={result.items} linked />;
    case "chart": return <section className="result-card">
      <p className="result-title">{result.title}</p>
      <ResultRenderBoundary key={raw}><Suspense fallback={<p role="status">正在加载图表…</p>}><ResultChartCanvas chart={result} /></Suspense></ResultRenderBoundary>
      <p className="result-muted">横轴：{result.x_label} · 单位：{result.unit}</p>
      {result.note ? <p>{result.note}</p> : null}
      <ResultSources items={result.sources} />
      <details className="result-details"><summary>查看绘图数据（{result.rows.length} 行）</summary><ResultTable title={result.title} columns={[result.x_label, ...result.series.map((name) => `${name}（${result.unit}）`)]} rows={result.rows} /></details>
    </section>;
  }
}
