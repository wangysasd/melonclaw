import type { Cell } from "./resultBlocks";

/** 字符串防止电子表格公式执行；实际数值（包括负数）保持数值。 */
export function spreadsheetCell(value: Cell): string {
  if (value === null) return "";
  const string = String(value);
  return typeof value === "string" && (/^\s*[=+\-@]/.test(string) || /^[\t\r]/.test(string)) ? `'${string}` : string;
}
export function tableCsv(rows: Cell[][]): string {
  return rows.map((row) => row.map((value) => `"${spreadsheetCell(value).replace(/"/g, '""')}"`).join(",")).join("\r\n");
}
export function tableTsv(rows: Cell[][]): string {
  return rows.map((row) => row.map((value) => spreadsheetCell(value).replace(/[\t\r\n]/g, " ")).join("\t")).join("\n");
}
export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = [...name].map((char) => char.charCodeAt(0) < 32 || /[\\/:*?"<>|]/.test(char) ? "_" : char).join("").slice(0, 150) || "result";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function downloadCsv(rows: Cell[][], title: string): void {
  downloadBlob(new Blob(["\ufeff", tableCsv(rows)], { type: "text/csv;charset=utf-8" }), `${title}.csv`);
}
