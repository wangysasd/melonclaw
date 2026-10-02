/** MCP 原文只用于本次请求；乐观消息、侧栏和失败草稿均使用脱敏文本。 */
export function mcpChatDisplay(content: string): string {
  const marker = /"(?:mcpServers|url|command|headers)"\s*:/;
  const fences = [...content.matchAll(/```(?:json)?\s*\n?([\s\S]*?)```/gi)]
    .filter(match => marker.test(match[1]));
  if (fences.length) {
    const display = content.replace(/```(?:json)?\s*\n?([\s\S]*?)```/gi, (block, body: string) =>
      marker.test(body) ? "[MCP 配置已提交；凭据不显示在聊天中]" : block);
    if (display.includes('"mcpServers"') || (display.includes('"headers"') && marker.test(display))) {
      return "[MCP 配置已提交；凭据不显示在聊天中]";
    }
    return display;
  }
  if ((content.includes("{") && marker.test(content)) ||
      content.includes('"mcpServers"') || (content.includes('"headers"') && marker.test(content))) {
    return "[MCP 配置已提交；凭据不显示在聊天中]";
  }
  return content;
}
