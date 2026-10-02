import { Component, lazy, memo, Suspense, useState, type ReactNode } from "react";
import { XMarkdown, type ComponentProps, type XMarkdownProps } from "@ant-design/x-markdown";
import "@ant-design/x-markdown/themes/light.css";

import { copyText } from "../lib/clipboard";
import { visibleAssistantText } from "../lib/toolSelection";
import { Icon } from "./Icon";
import { parseAssetRef } from "../lib/resultBlocks";
import { ResultAsset } from "./ResultAsset";
import { MarkdownTable } from "./ResultTable";
import { ResultCitationScope, useResultScope } from "./ResultContext";

const CodeHighlighter = lazy(() => import("@ant-design/x/es/code-highlighter"));
const MathMarkdown = lazy(() => import("./MathMarkdown"));
const ResultBlockView = lazy(() => import("./ResultBlockView"));
const MermaidDiagram = lazy(() => import("./MermaidDiagram"));

/** Markdown 扩展失败时只影响当前回复，并保留可读纯文本。 */
export class MarkdownBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function safeHref(value: unknown): string | undefined {
  const href = String(value ?? "").trim();
  if (!href || href.includes("\\") || [...href].some((char) => char.charCodeAt(0) <= 0x20)) return undefined;
  return /^(https?:|mailto:)/i.test(href) || href.startsWith("#") || (href.startsWith("/") && !href.startsWith("//"))
    ? href
    : undefined;
}

function decodeIncomplete(value: unknown): string {
  const encoded = String(value ?? "");
  try {
    return decodeURIComponent(encoded);
  } catch {
    return encoded;
  }
}

type IncompleteProps = ComponentProps & { "data-raw"?: string };

/** 保留尚未闭合的流式语法为文本，不让它消失，也不把它当作 HTML 执行。 */
function IncompleteMarkdown({ "data-raw": raw }: IncompleteProps) {
  return <span className="markdown-incomplete"><code>{decodeIncomplete(raw)}</code></span>;
}

function Code({ children, block, lang, streamStatus }: ComponentProps) {
  const [feedback, setFeedback] = useState("复制代码");
  const code = String(children ?? "");
  if (!block) return <code>{children}</code>;
  const fallback = <pre><code>{code}</code></pre>;
  const language = lang?.split(/\s/)[0] || "text";
  if (language.toLowerCase() === "melon-result") {
    return streamStatus === "done" ? <MarkdownBoundary fallback={fallback}><Suspense fallback={fallback}><ResultBlockView raw={code} /></Suspense></MarkdownBoundary>
      : <div className="result-pending"><span>结果正在生成…</span>{fallback}</div>;
  }
  return (
    <div className="markdown-code">
      <div className="markdown-code-head">
        <span>{language}</span>
        {!(language.toLowerCase() === "mermaid" && streamStatus === "done") && <button type="button" onClick={() => void copyText(code).then(() => setFeedback("已复制"), () => setFeedback("复制失败"))}>
          <Icon size={14} name="copy" />
          <span>{feedback}</span>
        </button>}
      </div>
      <MarkdownBoundary fallback={fallback}>
        <Suspense fallback={fallback}>
          {language.toLowerCase() === "mermaid" && streamStatus === "done" ? (
            <MermaidDiagram source={code} />
          ) : (
            <CodeHighlighter lang={language} header={false}>{code}</CodeHighlighter>
          )}
        </Suspense>
      </MarkdownBoundary>
    </div>
  );
}

function MarkdownImage({ src, alt }: ComponentProps & { src?: unknown }) {
  const source = String(src ?? "");
  const asset = source.startsWith("/attachments/") ? parseAssetRef({ attachment_id: source.slice(13) })
    : source.startsWith("/outputs/") ? parseAssetRef({ path: source }) : null;
  return asset ? <ResultAsset asset={asset} image caption={String(alt ?? "")} />
    : <span className="blocked-image">[图片：{String(alt || "未命名")}；外部或不受支持的图片地址未加载]</span>;
}
function MarkdownLink({ children, href }: LinkProps) {
  const scope = useResultScope();
  const value = String(href ?? "");
  const match = /^#source-([A-Za-z0-9_-]{1,40})$/.exec(value);
  const safe = safeHref(href);
  if (match && scope) return <a href={`#${scope.anchorPrefix}-${match[1]}`} onClick={(event) => {
    event.preventDefault();
    const target = document.getElementById(`${scope.anchorPrefix}-${match[1]}`);
    if (target instanceof HTMLDetailsElement) target.open = true;
    target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }}>{children}</a>;
  return safe ? <a href={safe} target={safe.startsWith("#") ? undefined : "_blank"} rel="noreferrer noopener">{children}</a> : <span>{children}</span>;
}
type LinkProps = ComponentProps & { href?: unknown };

export const markdownComponents: NonNullable<XMarkdownProps["components"]> = {
  code: Code,
  pre: ({ children }: ComponentProps) => <div className="markdown-pre">{children}</div>,
  a: MarkdownLink,
  img: MarkdownImage,
  p: ({ children }: ComponentProps) => <div className="markdown-paragraph">{children}</div>,
  table: ({ children }: ComponentProps) => <MarkdownTable>{children}</MarkdownTable>,
  "incomplete-link": IncompleteMarkdown,
  "incomplete-image": IncompleteMarkdown,
  "incomplete-html": IncompleteMarkdown,
  "incomplete-emphasis": IncompleteMarkdown,
  "incomplete-list": IncompleteMarkdown,
  "incomplete-table": IncompleteMarkdown,
  "incomplete-inline-code": IncompleteMarkdown,
};

export const Markdown = memo(function Markdown({ source, streaming = false }: { source: string; streaming?: boolean }) {
  const content = visibleAssistantText(source);
  const props: XMarkdownProps = {
    content,
    components: markdownComponents,
    escapeRawHtml: true,
    openLinksInNewTab: false,
    streaming: {
      hasNextChunk: streaming,
      tail: streaming,
      incompleteMarkdownComponentMap: {
        link: "incomplete-link",
        image: "incomplete-image",
        html: "incomplete-html",
        emphasis: "incomplete-emphasis",
        list: "incomplete-list",
        table: "incomplete-table",
        "inline-code": "incomplete-inline-code",
      },
    },
    className: "melon-markdown",
  };
  const fallback = <div className="markdown-fallback">{content}</div>;
  return (
    <ResultCitationScope>
    <MarkdownBoundary fallback={fallback}>
      {/\$|\\\(|\\\[/.test(content) ? (
        <Suspense fallback={<XMarkdown {...props} />}><MathMarkdown {...props} /></Suspense>
      ) : <XMarkdown {...props} />}
    </MarkdownBoundary>
    </ResultCitationScope>
  );
});
