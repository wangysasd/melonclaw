import { useEffect, useRef, useState } from "react";
import { fetchResultBlob, resultFileMetadata, resultFileUrl, type ResultFileMetadata } from "../api/results";
import { formatBytes } from "../lib/attachmentFiles";
import { downloadBlob } from "../lib/resultExport";
import type { AssetRef } from "../lib/resultBlocks";
import { useResultScope } from "./ResultContext";
import { useArtifacts } from "./ArtifactContext";
import { Icon } from "./Icon";

/** 所有地址由可信页面身份和明确引用构造；不使用模型提供的下载 URL。 */
export function ResultAsset({ asset, image = false, caption, compact = false }: { asset: AssetRef; image?: boolean; caption?: string; compact?: boolean }) {
  const scope = useResultScope();
  const artifacts = useArtifacts();
  const getMetadata = artifacts?.getMetadata;
  const identity = scope ? `${scope.userId}:${scope.conversationId}:${scope.projectId}` : "";
  const reference = "attachment_id" in asset ? asset.attachment_id : asset.path;
  const [state, setState] = useState<{ metadata?: ResultFileMetadata; error?: string; identity: string; reference: string }>({ identity: "", reference: "" });
  const actions = useRef<AbortController | null>(null);
  const [retry, setRetry] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!scope) return;
    const controller = new AbortController();
    const ref: AssetRef = reference.startsWith("/") ? { path: reference } : { attachment_id: reference };
    void (getMetadata && retry === 0 ? getMetadata(ref) : resultFileMetadata(ref, scope, controller.signal)).then((metadata) => {
      if (!controller.signal.aborted) setState({ metadata, identity, reference });
    }, (error: unknown) => {
      if (!controller.signal.aborted) setState({ identity, reference, error: error instanceof Error ? error.message : "无法读取成果。" });
    });
    return () => controller.abort();
  }, [scope, identity, reference, retry, getMetadata]);
  useEffect(() => {
    setBusy(false); setFeedback("");
    return () => actions.current?.abort();
  }, [identity, reference]);
  const current = state.identity === identity && state.reference === reference ? state : null;
  const metadata = current?.metadata;
  const url = scope ? resultFileUrl(asset, scope, true) : "";
  const openPreview = () => {
    if (scope) artifacts?.openArtifact(asset, scope.messageId, scope.conversationId);
  };
  const download = async () => {
    if (!scope || !metadata) return;
    const controller = new AbortController();
    actions.current?.abort(); actions.current = controller;
    setBusy(true); setFeedback("");
    try {
      const info = await resultFileMetadata(asset, scope, controller.signal);
      const blob = await fetchResultBlob(resultFileUrl(asset, scope), controller.signal);
      if (controller.signal.aborted) return;
      downloadBlob(blob, info.file_name);
    } catch (error) { if (!controller.signal.aborted) setFeedback(error instanceof Error ? error.message : "读取失败，请重试。"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  if (!scope) return <p className="result-muted">当前没有可用会话，无法打开文件。</p>;
  if (current?.error) return <div className="result-asset"><p>无法读取文件</p><p role="status">{current.error}</p><button type="button" onClick={() => { setState({ identity: "", reference: "" }); setRetry((value) => value + 1); }}>重新加载</button></div>;
  if (!metadata) return <div className="result-asset"><p>{reference.split("/").at(-1)}</p><p role="status">正在核验文件…</p></div>;
  if (image && metadata.preview_kind !== "image") return <p role="status">引用的文件不是可预览图片。</p>;
  if (compact) return <button type="button" className="workspace-file-row" onClick={openPreview} disabled={!artifacts}>
    <Icon name="file-text" size={18} /><span className="workspace-file-name">{metadata.file_name}</span>
  </button>;
  return <figure className={`result-asset${image ? "" : " artifact-file-card"}`} onClick={(event) => {
    if (!image && !(event.target instanceof Element && event.target.closest("button"))) openPreview();
  }}>
    {image ? <button type="button" className="result-image-thumb" aria-label={`放大 ${metadata.file_name}`} disabled={busy || !artifacts} onClick={openPreview}>
      <img src={url} alt={caption ?? metadata.file_name} loading="lazy" onError={() => setState({ identity, reference, error: "图片加载失败，文件可能已变更或不可访问。" })} />
    </button> : null}
    <figcaption>{!image ? <button type="button" className="artifact-file-main" aria-label={`查看 ${metadata.file_name}`} onClick={openPreview} disabled={!artifacts}>
      <span className="artifact-file-icon"><Icon name={metadata.preview_kind === "html" ? "globe-2" : "file-text"} size={24} /><span>{metadata.file_name.split(".").at(-1)?.toUpperCase()}</span></span>
      <span className="artifact-file-description"><span>{metadata.file_name}</span><span className="result-muted">{metadata.media_type} · {formatBytes(metadata.size_bytes)}</span></span>
    </button> : <><span>{metadata.file_name}</span><span className="result-muted">{metadata.media_type} · {formatBytes(metadata.size_bytes)}</span></>}{caption ? <p>{caption}</p> : null}</figcaption>
    <div className="result-actions">
      {artifacts ? <button type="button" disabled={busy} onClick={openPreview}>{metadata.preview_kind ? image ? "放大图片" : "查看" : "文件信息"}</button> : null}
      <button type="button" disabled={busy} onClick={() => void download()}>下载</button>
    </div>
    {feedback ? <p role="status">{feedback}</p> : null}
  </figure>;
}
