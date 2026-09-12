export interface SkillTrigger {
  start: number;
  end: number;
  query: string;
}

/**
 * 查找光标所在的 slash 查询片段。
 * 只识别行首或空白后的 `/`，避免把 URL、路径中的斜杠当成技能触发器。
 */
export function findSkillTrigger(
  value: string,
  cursor: number,
  selectionEnd = cursor,
): SkillTrigger | null {
  if (cursor !== selectionEnd || cursor < 0 || cursor > value.length) {
    return null;
  }
  const beforeCursor = value.slice(0, cursor);
  const match = /(?:^|\s)\/([^\s/]*)$/.exec(beforeCursor);
  if (!match) return null;
  const prefixLength = match[0].startsWith("/") ? 0 : 1;
  const start = beforeCursor.length - match[0].length + prefixLength;
  return { start, end: cursor, query: match[1] ?? "" };
}
