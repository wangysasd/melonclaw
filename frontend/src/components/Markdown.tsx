import { useState, type ReactNode } from "react";

import { copyText } from "../lib/clipboard";
import { Icon } from "./Icon";

/**
 * 轻量受控 Markdown 渲染：将受控文本转换为 React 元素输出。
 *
 * 覆盖：标题（h1–h3）、段落、无序/有序列表、引用、表格、围栏代码块，
 * 行内代码、粗体、斜体、链接；图片语法渲染为文本占位，不加载远程图片。
 * 链接仅允许 http(s)/mailto/本站相对路径；这不是通用 CommonMark，
 * 复杂内容按纯文本降级。React 文本节点天然转义，原始 HTML 不会进入 DOM。
 */

function safeHref(value: string): string {
  const href = String(value || "").trim();
  if (
    /^(https?:|mailto:)/i.test(href) ||
    href.startsWith("#") ||
    (href.startsWith("/") && !href.startsWith("//"))
  ) {
    return href;
  }
  return "";
}

const INLINE_PATTERN =
  /!\[([^\]]*)\]\([^)]*\)|`([^`\n]+)`|\[([^\]]+)\]\(([^)\s]+)(?:\s+["'][^)]*["'])?\)|\*\*([^*\n]+)\*\*|__([^_\n]+)__|\*([^*\n]+)\*|_([^_\n]+)_/g;

/** 行内渲染：图片占位、行内代码、安全链接、粗体、斜体。 */
function renderInline(source: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = new RegExp(INLINE_PATTERN);
  let lastIndex = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(source.slice(lastIndex, match.index));
    }
    const key = `${keyPrefix}-i${index++}`;
    if (match[1] !== undefined) {
      nodes.push(
        <span key={key} className="blocked-image">
          [图片：{match[1] || "未命名"}]
        </span>,
      );
    } else if (match[2] !== undefined) {
      nodes.push(<code key={key}>{match[2]}</code>);
    } else if (match[3] !== undefined) {
      const href = safeHref(match[4]);
      const label = renderInline(match[3], key);
      nodes.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noreferrer noopener">
            {label}
          </a>
        ) : (
          <span key={key}>{label}</span>
        ),
      );
    } else if (match[5] !== undefined || match[6] !== undefined) {
      nodes.push(<strong key={key}>{match[5] ?? match[6]}</strong>);
    } else {
      nodes.push(<em key={key}>{match[7] ?? match[8]}</em>);
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < source.length) {
    nodes.push(source.slice(lastIndex));
  }
  return nodes;
}

function parseTableRow(line: string): string[] {
  let value = line.trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|")) value = value.slice(0, -1);
  return value.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  const cells = parseTableRow(line);
  return cells.length >= 2 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [feedback, setFeedback] = useState<"idle" | "ok" | "fail">("idle");

  const handleCopy = async () => {
    try {
      await copyText(code);
      setFeedback("ok");
    } catch {
      setFeedback("fail");
    }
    window.setTimeout(() => setFeedback("idle"), 1800);
  };

  return (
    <div className="code-block">
      <div className="code-block-head">
        <span>{language || "code"}</span>
        <button type="button" className="code-copy" onClick={handleCopy}>
          <Icon size={14} name="copy" />
          <span>{feedback === "ok" ? "已复制" : feedback === "fail" ? "复制失败" : "复制代码"}</span>
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

const BLOCK_START =
  /^(\s*```|\s{0,3}#{1,3}\s|\s*[-*+]\s+|\s*\d+[.)]\s+|\s*>\s?)/;

/** 块级渲染：逐行解析为标题/代码块/表格/列表/引用/段落。 */
export function Markdown({ source }: { source: string }) {
  const lines = String(source || "")
    .replaceAll("\r\n", "\n")
    .split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let keyIndex = 0;
  const key = () => `b${keyIndex++}`;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = line.match(/^\s*```\s*([\w-]*)\s*$/);
    if (fence) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(<CodeBlock key={key()} code={code.join("\n")} language={fence[1]} />);
      continue;
    }
    const heading = line.match(/^\s*(#{1,3})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const level = heading[1].length;
      const content = renderInline(heading[2], key());
      if (level === 1) blocks.push(<h1 key={key()}>{content}</h1>);
      else if (level === 2) blocks.push(<h2 key={key()}>{content}</h2>);
      else blocks.push(<h3 key={key()}>{content}</h3>);
      index += 1;
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const headers = parseTableRow(line);
      index += 2;
      const rows: string[][] = [];
      while (
        index < lines.length &&
        lines[index].trim() &&
        lines[index].includes("|")
      ) {
        rows.push(parseTableRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <div className="markdown-table" key={key()}>
          <table>
            <thead>
              <tr>
                {headers.map((cell, cellIndex) => (
                  <th key={cellIndex}>{renderInline(cell, `${keyIndex}-${cellIndex}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {headers.map((_, cellIndex) => (
                    <td key={cellIndex}>
                      {renderInline(row[cellIndex] || "", `${keyIndex}-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    if (unordered) {
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const match = lines[index].match(/^\s*[-*+]\s+(.+)$/);
        if (!match) break;
        items.push(<li key={items.length}>{renderInline(match[1], `${keyIndex}-${items.length}`)}</li>);
        index += 1;
      }
      blocks.push(<ul key={key()}>{items}</ul>);
      continue;
    }
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ordered) {
      const items: ReactNode[] = [];
      while (index < lines.length) {
        const match = lines[index].match(/^\s*\d+[.)]\s+(.+)$/);
        if (!match) break;
        items.push(<li key={items.length}>{renderInline(match[1], `${keyIndex}-${items.length}`)}</li>);
        index += 1;
      }
      blocks.push(<ol key={key()}>{items}</ol>);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote: ReactNode[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(renderInline(lines[index].replace(/^\s*>\s?/, ""), `${keyIndex}-${quote.length}`));
        index += 1;
      }
      blocks.push(<blockquote key={key()}>{quote.map((node, i) => (i === 0 ? node : <><br key={`br${i}`} />{node}</>))}</blockquote>);
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !BLOCK_START.test(lines[index]) &&
      !(
        lines[index].includes("|") &&
        index + 1 < lines.length &&
        isTableSeparator(lines[index + 1])
      )
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={key()}>
        {paragraph.map((text, i) => (
          <span key={i}>
            {i > 0 && <br />}
            {renderInline(text, `${keyIndex}-${i}`)}
          </span>
        ))}
      </p>,
    );
  }

  return <>{blocks}</>;
}
