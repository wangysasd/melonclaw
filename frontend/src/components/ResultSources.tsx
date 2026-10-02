import { useId } from "react";
import type { ResultSource } from "../lib/resultBlocks";
import { safeExternalUrl } from "../lib/resultBlocks";
import { useResultScope } from "./ResultContext";
import { ResultAsset } from "./ResultAsset";

export function ResultSources({ items, linked = false }: { items: ResultSource[]; linked?: boolean }) {
  const scope = useResultScope();
  const local = useId().replace(/:/g, "");
  const prefix = scope?.anchorPrefix ?? `source-${local}`;
  return <div className="result-sources"><p className="result-muted">来源（回答提供）</p>{items.map((source) => {
    const url = safeExternalUrl(source.url);
    return <details className="result-details" id={linked ? `${prefix}-${source.id}` : undefined} key={source.id}>
      <summary><span>[{source.id}] {source.title}</span></summary>
      {url ? <p><a href={url} target="_blank" rel="noreferrer noopener">{url}</a></p> : null}
      {source.locator ? <p>{source.locator}</p> : null}
      {source.quote ? <blockquote>{source.quote}</blockquote> : null}
      {source.ref ? <ResultAsset asset={source.ref} /> : null}
      {!url && !source.ref ? <p className="result-muted">未提供可打开的来源链接。</p> : null}
    </details>;
  })}</div>;
}
