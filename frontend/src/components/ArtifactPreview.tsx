import { useEffect, useState } from "react";
import { fetchResultBlob, HTML_SOURCE_MARKER, htmlPreviewUrl, resultFileMetadata, resultFileUrl, type ResultFileMetadata } from "../api/results";
import type { FileRef } from "../lib/workspaceFiles";
import { Markdown } from "./Markdown";
import type { ResultScope } from "./ResultContext";

type Preview = { metadata: ResultFileMetadata; url?: string; text?: string; error?: string };

/** 内容只随文件选择或明确刷新变化；新回复不重载 iframe 内的交互。 */
export function ArtifactPreview({ asset, scope, revision, onMetadata, mode }: {
  asset: FileRef; scope: ResultScope; revision: number; onMetadata: (metadata: ResultFileMetadata) => void; mode: "preview" | "source";
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const reference = JSON.stringify(asset);
  const { userId, conversationId, projectId } = scope;
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setPreview(null); setError("");
    const identity = { userId, conversationId, projectId, anchorPrefix: "artifact-preview" };
    const ref = JSON.parse(reference) as FileRef;
    void (async () => {
      try {
        const metadata = await resultFileMetadata(ref, identity, controller.signal);
        if (controller.signal.aborted) return;
        onMetadata(metadata);
        if (!metadata.preview_kind) { setPreview({ metadata }); return; }
        const url = metadata.preview_kind === "html" ? htmlPreviewUrl(ref, identity) : resultFileUrl(ref, identity);
        const blob = await fetchResultBlob(url, controller.signal);
        if (controller.signal.aborted) return;
        let text: string | undefined;
        if (metadata.preview_kind === "text" || metadata.preview_kind === "html") {
          const limit = metadata.preview_kind === "html" ? 2_010_000 : 200_000;
          if (blob.size > limit) throw new Error("文件已变更或超过预览上限，请下载查看。");
          text = await blob.text();
          if (metadata.preview_kind === "html") {
            const start = text.indexOf(HTML_SOURCE_MARKER);
            if (start < 0) throw new Error("HTML 预览响应无效，请重试。");
            text = text.slice(start + HTML_SOURCE_MARKER.length);
          }
        }
        if (controller.signal.aborted) return;
        if (metadata.preview_kind !== "text") objectUrl = URL.createObjectURL(blob);
        setPreview({ metadata, url: objectUrl, text });
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "预览加载失败，请刷新或下载查看。");
      }
    })();
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [reference, userId, conversationId, projectId, revision, onMetadata]);

  if (error) return <div className="artifact-preview-state" role="alert">{error}</div>;
  if (!preview) return <div className="artifact-preview-state" role="status">正在加载产物…</div>;
  const { metadata } = preview;
  const markdown = metadata.preview_kind === "text" && /\.md$/i.test(metadata.file_name);
  return <div className="artifact-preview">
    <div className="artifact-preview-body">
      {!metadata.preview_kind ? <p className="artifact-preview-state">此文件类型、大小或编码暂不支持预览，请下载查看。</p> : null}
      {metadata.preview_kind === "html" ? <>
        <iframe hidden={mode !== "preview"} className="artifact-frame" sandbox="allow-scripts" allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'" referrerPolicy="no-referrer" title={metadata.file_name} src={preview.url} />
        {mode === "source" ? <pre className="artifact-source">{preview.text}</pre> : null}
      </> : metadata.preview_kind === "pdf" ? <iframe className="artifact-frame" sandbox="" title={metadata.file_name} src={preview.url} />
        : metadata.preview_kind === "image" ? <div className="artifact-image"><img src={preview.url} alt={metadata.file_name} /></div>
          : metadata.preview_kind === "text" ? markdown && mode === "preview" ? <div className="artifact-markdown"><Markdown source={preview.text ?? ""} /></div> : <pre className="artifact-source">{preview.text}</pre> : null}
    </div>
  </div>;
}
