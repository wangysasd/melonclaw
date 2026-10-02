import { useEffect, useRef, useState } from "react";
import { Modal } from "antd";
import { fetchResultBlob, resultFileMetadata, resultFileUrl, type ResultFileMetadata } from "../api/results";
import { formatBytes } from "../lib/attachmentFiles";
import { downloadBlob } from "../lib/resultExport";
import type { AssetRef } from "../lib/resultBlocks";
import { useResultScope } from "./ResultContext";

/** 所有地址由可信页面身份和明确引用构造；不使用模型提供的下载 URL。 */
export function ResultAsset({ asset, image = false, caption }: { asset: AssetRef; image?: boolean; caption?: string }) {
  const scope = useResultScope();
  const identity = scope ? `${scope.userId}:${scope.conversationId}:${scope.projectId}` : "";
  const reference = "attachment_id" in asset ? asset.attachment_id : asset.path;
  const [state, setState] = useState<{ metadata?: ResultFileMetadata; error?: string; identity: string; reference: string }>({ identity: "", reference: "" });
  const actions = useRef<AbortController | null>(null);
  const [retry, setRetry] = useState(0);
  const [preview, setPreview] = useState<{ url: string; kind: string; text?: string } | null>(null);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!scope) return;
    const controller = new AbortController();
    const ref: AssetRef = reference.startsWith("/") ? { path: reference } : { attachment_id: reference };
    void resultFileMetadata(ref, scope, controller.signal).then((metadata) => {
      if (!controller.signal.aborted) setState({ metadata, identity, reference });
    }, (error: unknown) => {
      if (!controller.signal.aborted) setState({ identity, reference, error: error instanceof Error ? error.message : "无法读取成果。" });
    });
    return () => controller.abort();
  }, [scope, identity, reference, retry]);
  useEffect(() => {
    setPreview(null); setBusy(false); setFeedback("");
    return () => actions.current?.abort();
  }, [identity, reference]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview.url); }, [preview]);
  const current = state.identity === identity && state.reference === reference ? state : null;
  const metadata = current?.metadata;
  const url = scope ? resultFileUrl(asset, scope, true) : "";
  const open = async (download: boolean) => {
    if (!scope || !metadata) return;
    const controller = new AbortController();
    actions.current?.abort(); actions.current = controller;
    setBusy(true); setFeedback("");
    try {
      const blob = await fetchResultBlob(resultFileUrl(asset, scope), controller.signal);
      if (controller.signal.aborted) return;
      if (download) downloadBlob(blob, metadata.file_name);
      else if (metadata.preview_kind === "text") {
        if (blob.size > 200_000) throw new Error("文件已变更或过大，请下载查看。");
        const text = await blob.text();
        if (controller.signal.aborted) return;
        setPreview({ url: URL.createObjectURL(blob), kind: "text", text });
      } else setPreview({ url: URL.createObjectURL(blob), kind: metadata.preview_kind ?? "" });
    } catch (error) { if (!controller.signal.aborted) setFeedback(error instanceof Error ? error.message : "读取失败，请重试。"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  };
  if (!scope) return <p className="result-muted">文件引用：{reference}（当前没有可用会话）</p>;
  if (current?.error) return <div className="result-asset"><p role="status">{current.error}</p><button type="button" onClick={() => { setState({ identity: "", reference: "" }); setRetry((value) => value + 1); }}>重新加载</button></div>;
  if (!metadata) return <p role="status">正在核验文件…</p>;
  if (image && metadata.preview_kind !== "image") return <p role="status">引用的文件不是可预览图片。</p>;
  return <figure className="result-asset">
    {image ? <button type="button" className="result-image-thumb" aria-label={`放大 ${metadata.file_name}`} disabled={busy} onClick={() => void open(false)}>
      <img src={url} alt={caption ?? metadata.file_name} loading="lazy" onError={() => setState({ identity, reference, error: "图片加载失败，文件可能已变更或不可访问。" })} />
    </button> : null}
    <figcaption><span>{metadata.file_name}</span><span className="result-muted">{metadata.media_type} · {formatBytes(metadata.size_bytes)}</span>{caption ? <p>{caption}</p> : null}</figcaption>
    <div className="result-actions">
      {metadata.preview_kind ? <button type="button" disabled={busy} onClick={() => void open(false)}>{image ? "放大图片" : "预览"}</button> : <span className="result-muted">此类型请下载查看</span>}
      <button type="button" disabled={busy} onClick={() => void open(true)}>下载</button>
    </div>
    {feedback ? <p role="status">{feedback}</p> : null}
    <Modal open={Boolean(preview)} title={metadata.file_name} footer={null} onCancel={() => setPreview(null)} width={900} destroyOnHidden>
      {preview?.kind === "image" ? <img className="result-image-preview" src={preview.url} alt={caption ?? metadata.file_name} /> : null}
      {preview?.kind === "text" ? <pre className="result-text-preview">{preview.text}</pre> : null}
      {preview?.kind === "pdf" ? <iframe className="result-pdf-preview" sandbox="" title={metadata.file_name} src={preview.url} /> : null}
    </Modal>
  </figure>;
}
