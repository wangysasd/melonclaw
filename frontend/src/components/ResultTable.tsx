import { useRef, useState, type ReactNode } from "react";
import { copyText } from "../lib/clipboard";
import { downloadCsv, tableTsv } from "../lib/resultExport";
import type { Cell } from "../lib/resultBlocks";

export function TableActions({ readRows, title = "表格" }: { readRows: () => Cell[][]; title?: string }) {
  const [feedback, setFeedback] = useState("复制表格");
  return <div className="result-actions">
    <button type="button" onClick={() => void copyText(tableTsv(readRows())).then(() => setFeedback("已复制"), () => setFeedback("复制失败"))}>{feedback}</button>
    <button type="button" onClick={() => downloadCsv(readRows(), title)}>下载 CSV</button>
  </div>;
}
export function MarkdownTable({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLTableElement>(null);
  return <div className="result-table-wrapper">
    <TableActions readRows={() => Array.from(ref.current?.rows ?? []).map((row) => Array.from(row.cells).map((cell) => cell.textContent ?? ""))} />
    <div className="markdown-table" tabIndex={0} aria-label="表格，可横向滚动"><table ref={ref}>{children}</table></div>
  </div>;
}
export function ResultTable({ columns, rows, title }: { columns: string[]; rows: Cell[][]; title: string }) {
  return <div className="result-table-wrapper">
    <TableActions title={title} readRows={() => [columns, ...rows]} />
    <div className="markdown-table" tabIndex={0} aria-label={`${title}，可横向滚动`}><table>
      <thead><tr>{columns.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column}>{cell === null ? "—" : String(cell)}</td>)}</tr>)}</tbody>
    </table></div>
  </div>;
}
