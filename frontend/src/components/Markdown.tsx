import { Component, lazy, memo, Suspense, useState, type ReactNode } from "react";
import { XMarkdown, type ComponentProps, type XMarkdownProps } from "@ant-design/x-markdown";
import "@ant-design/x-markdown/themes/light.css";

import { copyText } from "../lib/clipboard";
import { visibleAssistantText } from "../lib/toolSelection";
import { Icon } from "./Icon";

const CodeHighlighter = lazy(() => import("@ant-design/x/es/code-highlighter"));
const MathMarkdown = lazy(() => import("./MathMarkdown"));
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
  return (
    <div className="markdown-code">
      <div className="markdown-code-head">
        <span>{language}</span>
        <button type="button" onClick={() => void copyText(code).then(() => setFeedback("已复制"), () => setFeedback("复制失败"))}>
          <Icon size={14} name="copy" />
          <span>{feedback}</span>
        </button>
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

type LinkProps = ComponentProps & { href?: unknown };

export const markdownComponents: NonNullable<XMarkdownProps["components"]> = {
  code: Code,
  a: ({ children, href }: LinkProps) => {
    const safe = safeHref(href);
    return safe ? <a href={safe} target="_blank" rel="noreferrer noopener">{children}</a> : <span>{children}</span>;
  },
  img: ({ alt }: ComponentProps) => <span className="blocked-image">[图片：{String(alt || "未命名")}]</span>,
  table: ({ children }: ComponentProps) => <div className="markdown-table"><table>{children}</table></div>,
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
    <MarkdownBoundary fallback={fallback}>
      {/\$|\\\(|\\\[/.test(content) ? (
        <Suspense fallback={<XMarkdown {...props} />}><MathMarkdown {...props} /></Suspense>
      ) : <XMarkdown {...props} />}
    </MarkdownBoundary>
  );
});
