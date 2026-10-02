export type DiffLine = { kind: "same" | "added" | "removed"; text: string };
/** 有限 LCS 行差异；大内容使用整段替换，避免平方级开销。 */
export function textDiff(before: string, after: string): { lines: DiffLine[]; coarse: boolean } {
  const left = before === "" ? [] : before.split("\n");
  const right = after === "" ? [] : after.split("\n");
  if (left.length * right.length > 160_000 || left.length + right.length > 1000) {
    return { lines: [...left.map((text) => ({ kind: "removed" as const, text })), ...right.map((text) => ({ kind: "added" as const, text }))], coarse: true };
  }
  const grid = Array.from({ length: left.length + 1 }, () => new Uint16Array(right.length + 1));
  for (let i = left.length - 1; i >= 0; i--) for (let j = right.length - 1; j >= 0; j--) grid[i][j] = left[i] === right[j] ? grid[i + 1][j + 1] + 1 : Math.max(grid[i + 1][j], grid[i][j + 1]);
  const lines: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) { lines.push({ kind: "same", text: left[i++] }); j++; }
    else if (i < left.length && (j === right.length || grid[i + 1][j] >= grid[i][j + 1])) lines.push({ kind: "removed", text: left[i++] });
    else lines.push({ kind: "added", text: right[j++] });
  }
  return { lines, coarse: false };
}
