/** One-shot JSON import. Never persist or log input; reject duplicates before constructing objects. */
export function parseMcpJson(text: string): Record<string, unknown> {
  if (new TextEncoder().encode(text).length > 65536) throw new Error("JSON 不能超过 64 KiB。");
  let index = 0;
  const fail = (): never => { throw new Error(`JSON 格式无效，请检查第 ${index + 1} 个字符附近。`); };
  const space = () => { while (/[ \t\r\n]/.test(text[index] ?? "") && index < text.length) index++; };
  const string = (): string => {
    const start = index++;
    while (index < text.length) {
      const ch = text[index++];
      if (ch === "\\") index++;
      else if (ch === '"') {
        try { return JSON.parse(text.slice(start, index)) as string; } catch { return fail(); }
      }
    }
    return fail();
  };
  const value = (depth: number): unknown => {
    if (depth > 16) throw new Error("JSON 嵌套不能超过 16 层。");
    space();
    if (text[index] === '"') return string();
    if (text[index] === "{" || text[index] === "[") {
      const object = text[index++] === "{";
      const close = object ? "}" : "]";
      const result: Record<string, unknown> = Object.create(null);
      const array: unknown[] = [];
      space();
      if (text[index] === close) { index++; return object ? result : array; }
      while (index < text.length) {
        space();
        if (object) {
          if (text[index] !== '"') fail();
          const key = string();
          if (Object.hasOwn(result, key)) throw new Error("JSON 含重复键，请删除重复配置。");
          if (["__proto__", "constructor", "prototype"].includes(key)) throw new Error("JSON 含不允许的键。");
          space();
          if (text[index++] !== ":") fail();
          result[key] = value(depth + 1);
        } else array.push(value(depth + 1));
        space();
        if (text[index] === close) { index++; return object ? result : array; }
        if (text[index++] !== ",") fail();
      }
      return fail();
    }
    const match = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(index));
    if (!match) return fail();
    index += match[0].length;
    return JSON.parse(match[0]);
  };
  const decoded = value(0);
  space();
  if (index !== text.length) fail();
  if (!isObject(decoded)) throw new Error("MCP JSON 必须是对象。");
  return decoded;
}
function isObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
export interface ImportedMcp {
  name: string;
  transport: "http" | "sse" | "stdio" | "";
  url: string;
  command: string;
  args: string[];
  headers: Record<string, string>;
  env: Record<string, string>;
}
const aliases: Record<string, ImportedMcp["transport"]> = { http: "http", streamable_http: "http", "streamable-http": "http", sse: "sse", stdio: "stdio" };
export function mcpCandidates(text: string): { name: string; config: Record<string, unknown> }[] {
  const decoded = parseMcpJson(text);
  if ("mcpServers" in decoded) {
    if (Object.keys(decoded).length !== 1 || !isObject(decoded.mcpServers)) throw new Error("mcpServers 必须是唯一的顶层配置对象。");
    const entries = Object.entries(decoded.mcpServers);
    if (!entries.length) throw new Error("JSON 中没有 MCP 服务。");
    return entries.map(([name, config]) => {
      if (!isObject(config)) throw new Error("每个 MCP 服务配置必须是对象。");
      return { name, config };
    });
  }
  return [{ name: "", config: decoded }];
}
export function importMcp(candidate: { name: string; config: Record<string, unknown> }, isAdmin: boolean): ImportedMcp {
  const { config, name } = candidate;
  const allowed = new Set(["type", "transport", "url", "command", "args", "headers", "env"]);
  for (const key of Object.keys(config)) {
    if (!allowed.has(key)) throw new Error(`不支持字段 ${key.slice(0, 60)}；归属和启用状态由系统决定，请移除后重试。`);
  }
  const normalize = (value: unknown) => typeof value === "string" && Object.hasOwn(aliases, value) ? aliases[value] : undefined;
  const type = normalize(config.type);
  const transport = normalize(config.transport);
  if ((config.type !== undefined && !type) || (config.transport !== undefined && !transport) || (type && transport && type !== transport)) {
    throw new Error("type/transport 不支持或互相冲突。");
  }
  for (const key of ["url", "command"]) if (config[key] !== undefined && typeof config[key] !== "string") throw new Error(`${key} 必须是字符串。`);
  if (config.url && config.command) throw new Error("url 和 command 不能同时填写。");
  const selected = transport ?? type ?? (config.command ? "stdio" : "");
  if (selected === "stdio" && !isAdmin) throw new Error("个人 MCP 仅支持 HTTP/SSE，stdio 仅限管理员。");
  if (config.args !== undefined && (!Array.isArray(config.args) || config.args.some(item => typeof item !== "string"))) throw new Error("args 必须是字符串数组。");
  const map = (key: string): Record<string, string> => {
    const values = config[key] ?? {};
    if (!isObject(values) || Object.values(values).some(value => typeof value !== "string")) throw new Error(`${key} 必须是字符串键值对象。`);
    return values as Record<string, string>;
  };
  const headers = map("headers"), env = map("env");
  const keys = Object.keys(headers).map(key => key.toLowerCase());
  if (new Set(keys).size !== keys.length) throw new Error("请求头名称重复（不区分大小写）。");
  if (!isAdmin && JSON.stringify(config).includes("${")) throw new Error("个人配置不能引用服务器环境变量。");
  if (selected === "stdio" ? !!config.url || Object.keys(headers).length > 0 : !!config.command || (config.args as unknown[] | undefined)?.length || Object.keys(env).length > 0) throw new Error("连接类型与字段不一致。");
  return { name, transport: selected, url: String(config.url ?? ""), command: String(config.command ?? ""), args: (config.args ?? []) as string[], headers, env };
}
